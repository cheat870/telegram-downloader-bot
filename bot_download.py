import os
import json
import shutil
import threading
import uuid
import telebot
from yt_dlp import YoutubeDL
from typing import Any
from dotenv import load_dotenv
from datetime import datetime
from urllib.parse import urlparse

try:
    import psycopg
except ImportError:
    psycopg = None

load_dotenv()

API_TOKEN      = os.getenv("BOT_TOKEN")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID")
ADMIN_ID       = os.getenv("ADMIN_USER_ID")

if not API_TOKEN:
    raise ValueError("BOT_TOKEN not found!")
if not LOG_CHANNEL_ID:
    raise ValueError("LOG_CHANNEL_ID not found!")
if not ADMIN_ID:
    raise ValueError("ADMIN_USER_ID not found!")

try:
    LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
except ValueError:
    pass

try:
    ADMIN_ID = int(ADMIN_ID) if ADMIN_ID else None
except ValueError:
    ADMIN_ID = None

bot = telebot.TeleBot(API_TOKEN)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

COOKIES = {
    "tiktok":    "tiktok_cookies.txt",
    "facebook":  "fb_cookies.txt",
    "instagram": "ig_cookies.txt",
}

USERS_FILE = "users.json"
DATABASE_URL = os.getenv("DATABASE_URL")


def read_positive_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


MAX_VIDEO_HEIGHT = read_positive_int_env("MAX_VIDEO_HEIGHT", 2160)
MAX_FILE_SIZE_MB = read_positive_int_env("MAX_FILE_SIZE_MB", 2048)
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024
MAX_ITEMS_PER_REQUEST = read_positive_int_env("MAX_ITEMS_PER_REQUEST", 5)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}
SKIP_FILE_SUFFIXES = (
    ".part",
    ".tmp",
    ".temp",
    ".ytdl",
    ".json",
    ".description",
    ".info.json",
)


def quality_label() -> str:
    if MAX_VIDEO_HEIGHT >= 2160:
        return "4K"
    return f"{MAX_VIDEO_HEIGHT}p"


def format_size(size_bytes: int) -> str:
    size_mb = size_bytes / (1024 * 1024)
    if size_mb >= 1024:
        return f"{size_mb / 1024:.1f} GB"
    return f"{size_mb:.0f} MB"


# ═════════════════════════════════════════════
#  PERSISTENT USER STORAGE
# ═════════════════════════════════════════════

def database_enabled() -> bool:
    return bool(DATABASE_URL and psycopg is not None)


def ensure_users_table() -> bool:
    if not database_enabled():
        return False

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS bot_users (
                        user_id BIGINT PRIMARY KEY,
                        name TEXT NOT NULL DEFAULT '',
                        username TEXT NOT NULL DEFAULT '',
                        language_code TEXT NOT NULL DEFAULT '',
                        joined TEXT NOT NULL,
                        last_seen TEXT NOT NULL
                    )
                    """
                )
        return True
    except Exception as e:
        print(f"[DATABASE INIT ERROR] {e}")
        return False


def load_users_from_database() -> dict[int, dict]:
    if not ensure_users_table():
        return {}

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_id, name, username, language_code, joined, last_seen
                    FROM bot_users
                    """
                )
                return {
                    int(row[0]): {
                        "name": row[1],
                        "username": row[2],
                        "language_code": row[3],
                        "joined": row[4],
                        "last_seen": row[5],
                    }
                    for row in cur.fetchall()
                }
    except Exception as e:
        print(f"[DATABASE LOAD ERROR] {e}")
        return {}


def save_user_to_database(user_id: int, user_data: dict) -> bool:
    if not ensure_users_table():
        return False

    try:
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO bot_users (user_id, name, username, language_code, joined, last_seen)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (user_id) DO UPDATE SET
                        name = EXCLUDED.name,
                        username = EXCLUDED.username,
                        language_code = EXCLUDED.language_code,
                        last_seen = EXCLUDED.last_seen
                    """,
                    (
                        user_id,
                        user_data.get("name", ""),
                        user_data.get("username", ""),
                        user_data.get("language_code", ""),
                        user_data.get("joined", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                        user_data.get("last_seen", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    ),
                )
        return True
    except Exception as e:
        print(f"[DATABASE SAVE ERROR] {e}")
        return False


def load_users() -> dict[int, dict]:
    """Load users from JSON file. Returns {user_id: {name, username, joined}}"""
    database_users = load_users_from_database()
    if database_users:
        return database_users

    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            # JSON keys are always strings — convert back to int
            return {int(k): v for k, v in raw.items()}
        except Exception as e:
            print(f"[USERS LOAD ERROR] {e}")
    return {}


def save_users(users: dict[int, dict]) -> None:
    if database_enabled():
        saved_all = True
        for user_id, user_data in users.items():
            saved_all = save_user_to_database(user_id, user_data) and saved_all
        if saved_all:
            return

    try:
        tmp_file = f"{USERS_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, USERS_FILE)
    except Exception as e:
        print(f"[USERS SAVE ERROR] {e}")


# Load into memory on startup
all_users: dict[int, dict] = load_users()
notified_users: set[int] = set(all_users.keys())
users_lock = threading.Lock()


def user_full_name(user) -> str:
    full_name = user.first_name or ""
    if user.last_name:
        full_name += f" {user.last_name}"
    return full_name.strip() or "Unknown"


def store_user(user) -> bool:
    """Persist any user who talks to the bot, even if they skip /start."""
    if user is None or getattr(user, "id", None) is None:
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with users_lock:
        existing = all_users.get(user.id, {})
        was_new = user.id not in all_users
        all_users[user.id] = {
            "name": user_full_name(user),
            "username": user.username or "",
            "language_code": user.language_code or "",
            "joined": existing.get("joined", now),
            "last_seen": now,
        }
        save_users(all_users)
        if was_new:
            notified_users.add(user.id)
    return was_new


# ═════════════════════════════════════════════
#  USER JOIN NOTIFICATION
# ═════════════════════════════════════════════

def get_user_profile_photo(user_id: int):
    try:
        photos = bot.get_user_profile_photos(user_id, limit=1)
        if photos.total_count > 0:
            return photos.photos[0][0].file_id
    except Exception as e:
        print(f"[PHOTO ERROR] {e}")
    return None


def log_user_join(user):
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = f"@{user.username}" if user.username else "គ្មាន username"
    lang     = user.language_code.upper() if user.language_code else "?"

    full_name = user.first_name
    if user.last_name:
        full_name += f" {user.last_name}"

    caption = (
        f"👤 *New User*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📛 Name: {full_name}\n"
        f"🔖 Username: {username}\n"
        f"🆔 User ID: `{user.id}`\n"
        f"🌐 Language: {lang}\n"
        f"🕐 Time: {now}\n"
        f"━━━━━━━━━━━━━━━━"
    )

    try:
        photo_file_id = get_user_profile_photo(user.id)
        if photo_file_id:
            bot.send_photo(
                LOG_CHANNEL_ID,
                photo_file_id,
                caption=caption,
                parse_mode="Markdown"
            )
        else:
            bot.send_message(
                LOG_CHANNEL_ID,
                f"🖼️ _(No profile photo)_\n\n{caption}",
                parse_mode="Markdown"
            )
    except Exception as e:
        print(f"[JOIN LOG ERROR] {e}")
        print("[LOG HINT] ប្រាកដ​ថា Bot ជា Admin ក្នុង Channel + ID ចាប់ផ្ដើម -100...")


# ═════════════════════════════════════════════
#  DOWNLOAD LOG
# ═════════════════════════════════════════════

def log_download(user, url: str, status: str, platform: str = "?", size_mb: float = 0.0):
    now      = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = f"@{user.username}" if user.username else "គ្មាន username"
    emoji    = "✅" if status == "success" else "❌"

    log_msg = (
        f"{emoji} *Download Log*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 User: {user.first_name} ({username})\n"
        f"🆔 Chat ID: `{user.id}`\n"
        f"🌐 Platform: {platform.upper()}\n"
        f"🔗 URL: {url}\n"
        f"📦 Size: {size_mb:.2f} MB\n"
        f"📊 Status: {status}\n"
        f"🕐 Time: {now}\n"
        f"━━━━━━━━━━━━━━━━"
    )

    try:
        bot.send_message(LOG_CHANNEL_ID, log_msg, parse_mode="Markdown")
    except Exception as e:
        print(f"[LOG ERROR] {e}")
        print("[LOG HINT] ប្រាកដ​ថា Bot ជា Admin ក្នុង Channel + ID ចាប់ផ្ដើម -100...")


# ═════════════════════════════════════════════
#  PLATFORM DETECTION
# ═════════════════════════════════════════════

def detect_platform(url: str) -> str:
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    domains = {
        "youtube":     ("youtube.com", "youtu.be"),
        "tiktok":      ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com"),
        "facebook":    ("facebook.com", "fb.watch", "fb.com"),
        "instagram":   ("instagram.com",),
        "twitter":     ("twitter.com", "x.com"),
        "reddit":      ("reddit.com", "redd.it"),
        "vimeo":       ("vimeo.com",),
        "dailymotion": ("dailymotion.com", "dai.ly"),
        "twitch":      ("twitch.tv", "clips.twitch.tv"),
        "pinterest":   ("pinterest.com", "pin.it"),
        "threads":     ("threads.net",),
        "bilibili":    ("bilibili.com", "b23.tv"),
        "streamable":  ("streamable.com",),
        "rumble":      ("rumble.com",),
        "odysee":      ("odysee.com",),
        "soundcloud":  ("soundcloud.com",),
    }
    for platform, platform_domains in domains.items():
        if any(hostname == domain or hostname.endswith(f".{domain}") for domain in platform_domains):
            return platform
    return "generic"


def validate_url(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, "Invalid URL. Please send a valid http/https link."
    if parsed.username or parsed.password:
        return False, "URLs with embedded credentials are not allowed."
    if detect_platform(url) == "generic":
        return False, "Unsupported platform. Please send a link from a supported video site."
    return True, ""


# ═════════════════════════════════════════════
#  YT-DLP OPTIONS
# ═════════════════════════════════════════════

def build_ydl_opts(
    url: str,
    output_template: str,
    mobile_ua: bool = False,
    allow_playlist: bool = False,
) -> dict[str, Any]:
    platform = detect_platform(url)

    desktop_ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    mobile_user_agent = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
    ua = mobile_user_agent if mobile_ua else desktop_ua

    opts: dict[str, Any] = {
        "outtmpl":             output_template,
        "merge_output_format": "mp4",
        "noplaylist":          not allow_playlist,
        "max_filesize":        MAX_FILE_SIZE,
        "retries":             10,
        "fragment_retries":    10,
        "geo_bypass":          True,
        "socket_timeout":      30,
        "ignoreerrors":        allow_playlist,
        "http_headers": {
            "User-Agent":      ua,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        "postprocessors": [{
            "key":            "FFmpegVideoConvertor",
            "preferedformat": "mp4",
        }],
        "postprocessor_args": [
            "-movflags", "faststart",
            "-c:v", "copy",
            "-c:a", "aac",
            "-strict", "experimental",
        ],
    }
    if allow_playlist:
        opts["playlistend"] = MAX_ITEMS_PER_REQUEST

    if platform == "youtube":
        opts["format"] = (
            f"bestvideo[height<={MAX_VIDEO_HEIGHT}][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={MAX_VIDEO_HEIGHT}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={MAX_VIDEO_HEIGHT}]+bestaudio/"
            f"best[height<={MAX_VIDEO_HEIGHT}]/best"
        )
    elif platform == "tiktok":
        opts["format"] = "best"
        opts["http_headers"]["Referer"] = "https://www.tiktok.com/"
        opts["extractor_args"] = {
            "tiktok": {"api_hostname": ["api22-normal-c-alisg.tiktokv.com"]}
        }
        if os.path.exists(COOKIES["tiktok"]):
            opts["cookiefile"] = COOKIES["tiktok"]
    elif platform == "facebook":
        opts["format"] = "best[ext=mp4]/best"
        opts["http_headers"]["Referer"] = "https://www.facebook.com/"
        if os.path.exists(COOKIES["facebook"]):
            opts["cookiefile"] = COOKIES["facebook"]
    elif platform == "instagram":
        opts["format"] = "best[ext=mp4]/best"
        opts["http_headers"]["Referer"] = "https://www.instagram.com/"
        if os.path.exists(COOKIES["instagram"]):
            opts["cookiefile"] = COOKIES["instagram"]
    elif platform == "twitter":
        opts["format"] = f"bestvideo[height<={MAX_VIDEO_HEIGHT}]+bestaudio/best[height<={MAX_VIDEO_HEIGHT}]/best"
    elif platform == "reddit":
        opts["format"] = "best[ext=mp4]/best"
    elif platform == "twitch":
        opts["format"] = f"best[height<={MAX_VIDEO_HEIGHT}][ext=mp4]/best[height<={MAX_VIDEO_HEIGHT}]/best"
    elif platform in ("vimeo", "bilibili"):
        opts["format"] = f"bestvideo[height<={MAX_VIDEO_HEIGHT}]+bestaudio/best[height<={MAX_VIDEO_HEIGHT}]/best"
    else:
        opts["format"] = (
            f"bestvideo[height<={MAX_VIDEO_HEIGHT}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={MAX_VIDEO_HEIGHT}]+bestaudio/"
            f"best[height<={MAX_VIDEO_HEIGHT}][ext=mp4]/"
            f"best[height<={MAX_VIDEO_HEIGHT}]/best"
        )

    return opts


def is_sendable_file(path: str) -> bool:
    lower = path.lower()
    if any(lower.endswith(suffix) for suffix in SKIP_FILE_SUFFIXES):
        return False
    return os.path.isfile(path)


def collect_downloaded_files(download_dir: str) -> list[str]:
    try:
        files = [
            os.path.join(download_dir, f)
            for f in os.listdir(download_dir)
            if is_sendable_file(os.path.join(download_dir, f))
        ]
    except Exception:
        return []
    return sorted(files, key=os.path.getctime)


def resolve_file_paths(ydl: YoutubeDL, info_dict: dict, download_dir: str) -> list[str]:
    candidates: list[str] = []

    def add_candidate(info: dict | None) -> None:
        if not info:
            return
        try:
            path = ydl.prepare_filename(info)
        except Exception:
            return
        candidates.append(path)
        candidates.append(os.path.splitext(path)[0] + ".mp4")

    if isinstance(info_dict, dict) and info_dict.get("entries"):
        for entry in info_dict.get("entries") or []:
            add_candidate(entry)
    else:
        add_candidate(info_dict)

    resolved: list[str] = []
    seen: set[str] = set()
    for path in candidates + collect_downloaded_files(download_dir):
        if path in seen or not is_sendable_file(path):
            continue
        seen.add(path)
        resolved.append(path)

    return resolved


def resolve_file_path(ydl: YoutubeDL, info_dict: dict, download_dir: str) -> str | None:
    paths = resolve_file_paths(ydl, info_dict, download_dir)
    if paths:
        return paths[0]
    path = ydl.prepare_filename(info_dict)
    if os.path.exists(path):
        return path
    mp4 = os.path.splitext(path)[0] + ".mp4"
    if os.path.exists(mp4):
        return mp4
    try:
        files = [
            os.path.join(download_dir, f)
            for f in os.listdir(download_dir)
            if os.path.isfile(os.path.join(download_dir, f))
        ]
        if files:
            return max(files, key=os.path.getctime)
    except Exception:
        pass
    return None


def attempt_download(
    url: str,
    output_template: str,
    download_dir: str,
    allow_playlist: bool = False,
) -> tuple[list[str], str | None]:
    last_error = "Unknown error"

    for mobile_ua in [False, True]:
        label = "Mobile UA" if mobile_ua else "Desktop UA"
        print(f"[ATTEMPT] {label}")
        try:
            opts = build_ydl_opts(
                url,
                output_template,
                mobile_ua=mobile_ua,
                allow_playlist=allow_playlist,
            )
            with YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
                info = ydl.extract_info(url, download=True)
                paths = resolve_file_paths(ydl, info, download_dir)
                if paths:
                    return paths[:MAX_ITEMS_PER_REQUEST], None
        except Exception as e:
            last_error = str(e)
            print(f"[FAILED] {label} → {last_error[:120]}")

    print("[ATTEMPT] Ultra-bare fallback")
    try:
        platform = detect_platform(url)
        bare: dict[str, Any] = {
            "outtmpl":    output_template,
            "format":     "best",
            "noplaylist": not allow_playlist,
            "max_filesize": MAX_FILE_SIZE,
            "retries":    5,
            "geo_bypass": True,
            "ignoreerrors": allow_playlist,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.6367.82 Mobile Safari/537.36"
                ),
            },
        }
        if allow_playlist:
            bare["playlistend"] = MAX_ITEMS_PER_REQUEST
        if platform in COOKIES and os.path.exists(COOKIES[platform]):
            bare["cookiefile"] = COOKIES[platform]
        with YoutubeDL(bare) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(url, download=True)
            paths = resolve_file_paths(ydl, info, download_dir)
            if paths:
                return paths[:MAX_ITEMS_PER_REQUEST], None
    except Exception as e:
        last_error = str(e)
        print(f"[FAILED] Ultra-bare → {last_error[:120]}")

    return [], last_error


def friendly_error(err: str, platform: str) -> str:
    e = err.lower()
    if "unsupported url" in e:
        return "❌ Platform នេះ​មិន​ Support ទេ។"
    if "private" in e:
        return "❌ វីដេអូ​នេះ​ជា Private — មើល​មិន​បាន។"
    if "login" in e or "sign in" in e:
        return "❌ វីដេអូ​នេះ​ត្រូវ Login មើល។\n💡 Export cookies ពី Browser ហើយ​ដាក់ក្នុង Folder Bot។"
    if "unavailable" in e or "removed" in e or "deleted" in e:
        return "❌ វីដេអូ​នេះ​ត្រូវ​បាន​លុប ឬ​មិន​មាន​ទៀត​ហើយ។"
    if "timed out" in e or "timeout" in e:
        return "❌ Connection timeout — សូម​ព្យាយាម​ម្ដង​ទៀត។"
    if "403" in e:
        return (
            f"❌ Access ត្រូវ​បាន​បដិសេធ (403)。\n"
            f"💡 ត្រូវ​ការ cookies file: {COOKIES.get(platform, 'cookies.txt')}"
        )
    if "extract" in e or "webpage" in e or "unable to extract" in e:
        cookie_file = COOKIES.get(platform, "cookies.txt")
        return (
            f"❌ {platform.upper()} Download បរាជ័យ។\n\n"
            f"💡 *Fix:* Export cookies ពី Browser:\n"
            f"1. Install: *Get cookies.txt LOCALLY*\n"
            f"2. Login {platform}.com\n"
            f"3. Export → Save ជា `{cookie_file}`\n"
            f"   ក្នុង `D:\\Bot\\TelegramBot\\`"
        )
    return f"❌ Error: {err[:200]}"


def send_downloaded_media(chat_id: int, file_path: str, caption: str) -> None:
    ext = os.path.splitext(file_path)[1].lower()
    with open(file_path, "rb") as media:
        if ext in VIDEO_EXTENSIONS:
            bot.send_video(
                chat_id,
                media,
                timeout=600,
                supports_streaming=True,
                caption=caption,
            )
        elif ext in PHOTO_EXTENSIONS:
            try:
                bot.send_photo(chat_id, media, timeout=600, caption=caption)
            except Exception:
                media.seek(0)
                bot.send_document(chat_id, media, timeout=600, caption=caption)
        elif ext in AUDIO_EXTENSIONS:
            bot.send_audio(chat_id, media, timeout=600, caption=caption)
        else:
            bot.send_document(chat_id, media, timeout=600, caption=caption)


def extract_request_url(message, allow_playlist: bool) -> str:
    text = (message.text or "").strip()
    if allow_playlist:
        return text.partition(" ")[2].strip()
    return text


def process_download_request(message, allow_playlist: bool = False) -> None:
    user = message.from_user
    if user is None:
        bot.reply_to(message, "Please send the link from a user chat.")
        return

    was_new = store_user(user)
    if was_new:
        log_user_join(user)

    url = extract_request_url(message, allow_playlist)
    if not url:
        bot.reply_to(message, f"Usage: /all <video or playlist URL> (max {MAX_ITEMS_PER_REQUEST} files)")
        return

    is_valid, validation_error = validate_url(url)
    if not is_valid:
        bot.reply_to(message, validation_error)
        return

    platform = detect_platform(url)
    request_label = f"ALL up to {MAX_ITEMS_PER_REQUEST}" if allow_playlist else quality_label()

    msg = bot.send_message(
        message.chat.id,
        f"⏳ កំពុង​ដំណើរការ... [{platform.upper()}]"
    )

    download_dir = os.path.join(DOWNLOAD_FOLDER, f"{user.id}_{message.message_id}_{uuid.uuid4().hex[:8]}")
    os.makedirs(download_dir, exist_ok=True)
    output_template = os.path.join(download_dir, "%(autonumber)03d_%(title).80s_%(id)s.%(ext)s")

    try:
        bot.edit_message_text(
            f"⬇️ កំពុង Download... [{platform.upper()} | {request_label}]",
            message.chat.id, msg.message_id
        )

        file_paths, err = attempt_download(
            url,
            output_template,
            download_dir,
            allow_playlist=allow_playlist,
        )

        if not file_paths:
            reply = friendly_error(err or "Unknown error", platform)
            bot.edit_message_text(reply, message.chat.id, msg.message_id, parse_mode="Markdown")
            log_download(user, url, f"error: {(err or '')[:100]}", platform, 0.0)
            return

        sent = 0
        failed = 0
        skipped = 0
        total_size_mb = 0.0
        total_files = len(file_paths)

        for index, file_path in enumerate(file_paths, start=1):
            try:
                file_size = os.path.getsize(file_path)
                size_mb = file_size / (1024 * 1024)
            except OSError:
                failed += 1
                continue

            if file_size > MAX_FILE_SIZE:
                skipped += 1
                continue

            bot.edit_message_text(
                f"📤 កំពុង Upload... {index} / {total_files}",
                message.chat.id, msg.message_id
            )

            caption_parts = [
                f"✅ {size_mb:.1f} MB",
                platform.upper(),
                quality_label(),
            ]
            if allow_playlist:
                caption_parts.append(f"{index}/{total_files}")
            caption = " | ".join(caption_parts)

            uploaded = False
            for attempt in range(3):
                try:
                    send_downloaded_media(message.chat.id, file_path, caption)
                    uploaded = True
                    break
                except Exception as upload_err:
                    if attempt == 2:
                        failed += 1
                        print(f"[UPLOAD ERROR] {str(upload_err)[:150]}")
                    else:
                        bot.edit_message_text(
                            f"⚠️ Upload ម្ដង​ទៀត {attempt + 1}/3... ({index}/{total_files})",
                            message.chat.id, msg.message_id
                        )

            if uploaded:
                sent += 1
                total_size_mb += size_mb

        if sent:
            bot.edit_message_text(
                f"✅ រួច​រាល់! Sent: {sent}, skipped: {skipped}, failed: {failed}",
                message.chat.id, msg.message_id
            )
            log_download(user, url, f"success ({sent} sent, {skipped} skipped, {failed} failed)", platform, total_size_mb)
        else:
            bot.edit_message_text(
                f"❌ Upload បរាជ័យ។ skipped: {skipped}, failed: {failed}",
                message.chat.id, msg.message_id
            )
            log_download(user, url, f"upload failed ({skipped} skipped, {failed} failed)", platform, total_size_mb)
    finally:
        shutil.rmtree(download_dir, ignore_errors=True)


# ═════════════════════════════════════════════
#  BOT HANDLERS
# ═════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def send_welcome(message):
    user = message.from_user

    if store_user(user):
        log_user_join(user)

    bot.reply_to(
        message,
        "👋 *សួស្ដី!* ផ្ញើ link វីដេអូ ខ្ញុំ​នឹង Download ជូន!\n\n"
        "✅ *Platforms ដែល Support:*\n"
        "• YouTube • TikTok • Facebook\n"
        "• Instagram • Twitter/X • Reddit\n"
        "• Vimeo • Dailymotion • Twitch\n"
        "• Bilibili • Streamable • Rumble\n"
        "• Pinterest • Threads • និងច្រើន​ទៀត!\n\n"
        f"📚 Download all: `/all link` (max {MAX_ITEMS_PER_REQUEST} files)\n"
        "🖼️ TikTok photo/live-photo posts are supported too.\n\n"
        f"📽️ Format: MP4  |  Quality: up to {quality_label()}  |  Max: {format_size(MAX_FILE_SIZE)}",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["help"])
def send_help(message):
    user = message.from_user
    if store_user(user):
        log_user_join(user)

    bot.reply_to(
        message,
        "👋 *Video Downloader Bot*\n\n"
        "✅ *Platforms ដែល Support:*\n"
        "• YouTube • TikTok • Facebook\n"
        "• Instagram • Twitter/X • Reddit\n"
        "• Vimeo • Dailymotion • Twitch\n"
        "• Bilibili • Streamable • Rumble\n"
        "• Pinterest • Threads • និងច្រើន​ទៀត!\n\n"
        f"📽️ Format: MP4  |  Quality: up to {quality_label()}  |  Max: {format_size(MAX_FILE_SIZE)}\n\n"
        f"📚 Download all: `/all link` (max {MAX_ITEMS_PER_REQUEST} files)\n"
        "🖼️ TikTok photo/live-photo posts are supported too.\n\n"
        "⚠️ *TikTok/FB Private* → ត្រូវ​ការ cookies file\n\n"
        "💡 គ្រាន់​តែ​ paste link វីដេអូ​មក!",
        parse_mode="Markdown"
    )


# ═════════════════════════════════════════════
#  ADMIN: BROADCAST
# ═════════════════════════════════════════════

@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    store_user(message.from_user)
    if ADMIN_ID is None or message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ អ្នក​មិន​មាន​សិទ្ធិ​ប្រើ​ command នេះ​ទេ។")
        return

    # Extract the broadcast text (everything after /broadcast)
    text = (message.text or "").partition(" ")[2].strip()
    if not text:
        bot.reply_to(
            message,
            "⚠️ *របៀប​ប្រើ:*\n`/broadcast សារ​ដែល​ចង់​ផ្ញើ`\n\n"
            "ឧទាហរណ៍:\n`/broadcast Bot នឹង​ Maintenance ម៉ោង 10 យប់​!`",
            parse_mode="Markdown"
        )
        return

    with users_lock:
        user_ids = list(all_users.keys())
    total    = len(user_ids)

    if total == 0:
        bot.reply_to(message, "⚠️ មិន​មាន​អ្នក​ប្រើ​ណា​ម្នាក់​ទេ​នៅ​ឡើយ។")
        return

    status_msg = bot.reply_to(message, f"📤 កំពុង​ផ្ញើ​ទៅ 0 / {total} នាក់...")

    sent    = 0
    failed  = 0
    blocked = 0

    broadcast_text = (
        f"📢 *សារ​ពី Admin*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{text}\n"
        f"━━━━━━━━━━━━━━━━"
    )

    for uid in user_ids:
        try:
            bot.send_message(uid, broadcast_text, parse_mode="Markdown")
            sent += 1
        except Exception as e:
            err = str(e).lower()
            if "blocked" in err or "deactivated" in err or "not found" in err or "403" in err:
                blocked += 1
            else:
                failed += 1
        # Update status every 20 users to avoid flood
        if (sent + failed + blocked) % 20 == 0:
            try:
                bot.edit_message_text(
                    f"📤 កំពុង​ផ្ញើ... {sent + failed + blocked} / {total} នាក់",
                    message.chat.id,
                    status_msg.message_id
                )
            except Exception:
                pass

    summary = (
        f"✅ *Broadcast រួច​រាល់!*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👥 សរុប: {total} នាក់\n"
        f"✅ ផ្ញើ​បាន: {sent} នាក់\n"
        f"🚫 Block/លុប: {blocked} នាក់\n"
        f"❌ Error: {failed} នាក់\n"
        f"━━━━━━━━━━━━━━━━"
    )
    bot.edit_message_text(summary, message.chat.id, status_msg.message_id, parse_mode="Markdown")


# ═════════════════════════════════════════════
#  ADMIN: STATS
# ═════════════════════════════════════════════

@bot.message_handler(commands=["stats"])
def stats(message):
    store_user(message.from_user)
    if ADMIN_ID is None or message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ អ្នក​មិន​មាន​សិទ្ធិ​ប្រើ​ command នេះ​ទេ។")
        return

    with users_lock:
        total = len(all_users)
    bot.reply_to(
        message,
        f"📊 *Bot Statistics*\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👥 Users សរុប: *{total} នាក់*\n"
        f"━━━━━━━━━━━━━━━━",
        parse_mode="Markdown"
    )


# ═════════════════════════════════════════════
#  MAIN DOWNLOAD HANDLER
# ═════════════════════════════════════════════

@bot.message_handler(commands=["all"])
def download_all_videos(message):
    process_download_request(message, allow_playlist=True)


@bot.message_handler(func=lambda message: True)
def download_video(message):
    if not getattr(message, "text", None):
        if message.from_user:
            store_user(message.from_user)
        bot.reply_to(message, "Please send a video link.")
        return

    process_download_request(message, allow_playlist=False)


print("Bot is running...")
bot.infinity_polling()
