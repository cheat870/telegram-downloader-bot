import os
import re
import json
import telebot
from yt_dlp import YoutubeDL
from typing import Any
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()

API_TOKEN      = os.getenv("BOT_TOKEN", "BOT_TOKEN")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "LOG_CHANNEL_ID")
ADMIN_ID       = os.getenv("ADMIN_USER_ID", "1667275809")

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


# ═════════════════════════════════════════════
#  PERSISTENT USER STORAGE
# ═════════════════════════════════════════════

def load_users() -> dict[int, dict]:
    """Load users from JSON file. Returns {user_id: {name, username, joined}}"""
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
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[USERS SAVE ERROR] {e}")


# Load into memory on startup
all_users: dict[int, dict] = load_users()
notified_users: set[int] = set(all_users.keys())


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
    patterns = {
        "youtube":     r"(youtube\.com|youtu\.be)",
        "tiktok":      r"(tiktok\.com|vm\.tiktok\.com|vt\.tiktok\.com)",
        "facebook":    r"(facebook\.com|fb\.watch|fb\.com)",
        "instagram":   r"(instagram\.com)",
        "twitter":     r"(twitter\.com|x\.com|t\.co)",
        "reddit":      r"(reddit\.com|redd\.it)",
        "vimeo":       r"(vimeo\.com)",
        "dailymotion": r"(dailymotion\.com|dai\.ly)",
        "twitch":      r"(twitch\.tv|clips\.twitch\.tv)",
        "pinterest":   r"(pinterest\.com|pin\.it)",
        "threads":     r"(threads\.net)",
        "bilibili":    r"(bilibili\.com|b23\.tv)",
        "streamable":  r"(streamable\.com)",
        "rumble":      r"(rumble\.com)",
        "odysee":      r"(odysee\.com)",
        "soundcloud":  r"(soundcloud\.com)",
    }
    for platform, pattern in patterns.items():
        if re.search(pattern, url, re.IGNORECASE):
            return platform
    return "generic"


# ═════════════════════════════════════════════
#  YT-DLP OPTIONS
# ═════════════════════════════════════════════

def build_ydl_opts(url: str, output_template: str, mobile_ua: bool = False) -> dict[str, Any]:
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
        "noplaylist":          True,
        "retries":             10,
        "fragment_retries":    10,
        "geo_bypass":          True,
        "socket_timeout":      30,
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

    if platform == "youtube":
        opts["format"] = (
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=720]+bestaudio/"
            "best[height<=720]/best"
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
        opts["format"] = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    elif platform == "reddit":
        opts["format"] = "best[ext=mp4]/best"
    elif platform == "twitch":
        opts["format"] = "best[height<=720][ext=mp4]/best[height<=720]/best"
    elif platform in ("vimeo", "bilibili"):
        opts["format"] = "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
    else:
        opts["format"] = (
            "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/"
            "bestvideo[height<=720]+bestaudio/"
            "best[height<=720][ext=mp4]/"
            "best[height<=720]/best"
        )

    return opts


def resolve_file_path(ydl: YoutubeDL, info_dict: dict) -> str | None:
    path = ydl.prepare_filename(info_dict)
    if os.path.exists(path):
        return path
    mp4 = os.path.splitext(path)[0] + ".mp4"
    if os.path.exists(mp4):
        return mp4
    try:
        files = [
            os.path.join(DOWNLOAD_FOLDER, f)
            for f in os.listdir(DOWNLOAD_FOLDER)
            if os.path.isfile(os.path.join(DOWNLOAD_FOLDER, f))
        ]
        if files:
            return max(files, key=os.path.getctime)
    except Exception:
        pass
    return None


def attempt_download(url: str, output_template: str) -> tuple[str | None, str | None]:
    last_error = "Unknown error"

    for mobile_ua in [False, True]:
        label = "Mobile UA" if mobile_ua else "Desktop UA"
        print(f"[ATTEMPT] {label}")
        try:
            opts = build_ydl_opts(url, output_template, mobile_ua=mobile_ua)
            with YoutubeDL(opts) as ydl:  # type: ignore[arg-type]
                info = ydl.extract_info(url, download=True)
                path = resolve_file_path(ydl, info)
                if path:
                    return path, None
        except Exception as e:
            last_error = str(e)
            print(f"[FAILED] {label} → {last_error[:120]}")

    print("[ATTEMPT] Ultra-bare fallback")
    try:
        platform = detect_platform(url)
        bare: dict[str, Any] = {
            "outtmpl":    output_template,
            "format":     "best",
            "noplaylist": True,
            "retries":    5,
            "geo_bypass": True,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.6367.82 Mobile Safari/537.36"
                ),
            },
        }
        if platform in COOKIES and os.path.exists(COOKIES[platform]):
            bare["cookiefile"] = COOKIES[platform]
        with YoutubeDL(bare) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(url, download=True)
            path = resolve_file_path(ydl, info)
            if path:
                return path, None
    except Exception as e:
        last_error = str(e)
        print(f"[FAILED] Ultra-bare → {last_error[:120]}")

    return None, last_error


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


# ═════════════════════════════════════════════
#  BOT HANDLERS
# ═════════════════════════════════════════════

@bot.message_handler(commands=["start"])
def send_welcome(message):
    user = message.from_user

    if user.id not in notified_users:
        notified_users.add(user.id)
        # Save new user to persistent storage
        all_users[user.id] = {
            "name":     f"{user.first_name}{' ' + user.last_name if user.last_name else ''}",
            "username": user.username or "",
            "joined":   datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_users(all_users)
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
        "📽️ Format: MP4  |  Max: 50 MB",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=["help"])
def send_help(message):
    bot.reply_to(
        message,
        "👋 *Video Downloader Bot*\n\n"
        "✅ *Platforms ដែល Support:*\n"
        "• YouTube • TikTok • Facebook\n"
        "• Instagram • Twitter/X • Reddit\n"
        "• Vimeo • Dailymotion • Twitch\n"
        "• Bilibili • Streamable • Rumble\n"
        "• Pinterest • Threads • និងច្រើន​ទៀត!\n\n"
        "📽️ Format: MP4  |  Max: 50 MB\n\n"
        "⚠️ *TikTok/FB Private* → ត្រូវ​ការ cookies file\n\n"
        "💡 គ្រាន់​តែ​ paste link វីដេអូ​មក!",
        parse_mode="Markdown"
    )


# ═════════════════════════════════════════════
#  ADMIN: BROADCAST
# ═════════════════════════════════════════════

@bot.message_handler(commands=["broadcast"])
def broadcast(message):
    if ADMIN_ID is None or message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ អ្នក​មិន​មាន​សិទ្ធិ​ប្រើ​ command នេះ​ទេ។")
        return

    # Extract the broadcast text (everything after /broadcast)
    text = message.text.partition(" ")[2].strip()
    if not text:
        bot.reply_to(
            message,
            "⚠️ *របៀប​ប្រើ:*\n`/broadcast សារ​ដែល​ចង់​ផ្ញើ`\n\n"
            "ឧទាហរណ៍:\n`/broadcast Bot នឹង​ Maintenance ម៉ោង 10 យប់​!`",
            parse_mode="Markdown"
        )
        return

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
    if ADMIN_ID is None or message.from_user.id != ADMIN_ID:
        bot.reply_to(message, "⛔ អ្នក​មិន​មាន​សិទ្ធិ​ប្រើ​ command នេះ​ទេ។")
        return

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

@bot.message_handler(func=lambda message: True)
def download_video(message):
    url      = message.text.strip()
    user     = message.from_user
    platform = detect_platform(url)

    if not url.startswith("http"):
        bot.reply_to(message, "⚠️ សូម​ផ្ញើ URL ត្រឹម​ត្រូវ (ចាប់ផ្ដើម​ដោយ http/https)")
        return

    msg = bot.send_message(
        message.chat.id,
        f"⏳ កំពុង​ដំណើរការ... [{platform.upper()}]"
    )

    size_mb : float = 0.0
    output_template = f"{DOWNLOAD_FOLDER}/%(title).80s.%(ext)s"

    bot.edit_message_text(
        f"⬇️ កំពុង Download... [{platform.upper()}]",
        message.chat.id, msg.message_id
    )

    file_path, err = attempt_download(url, output_template)

    if file_path is None:
        reply = friendly_error(err or "Unknown error", platform)
        bot.edit_message_text(reply, message.chat.id, msg.message_id, parse_mode="Markdown")
        log_download(user, url, f"error: {(err or '')[:100]}", platform, 0.0)
        return

    try:
        file_size = os.path.getsize(file_path)
        size_mb   = file_size / (1024 * 1024)
    except OSError:
        bot.edit_message_text("❌ File រក​មិន​ឃើញ​ក្រោយ Download។", message.chat.id, msg.message_id)
        log_download(user, url, "error: file missing", platform)
        return

    if file_size > 50 * 1024 * 1024:
        bot.edit_message_text(
            f"❌ វីដេអូ​ធំ​ពេក ({size_mb:.1f} MB)\n"
            f"Telegram ទទួល​បាន​តែ 50 MB ប៉ុណ្ណោះ។",
            message.chat.id, msg.message_id
        )
        log_download(user, url, f"too large ({size_mb:.1f}MB)", platform, size_mb)
        os.remove(file_path)
        return

    bot.edit_message_text("📤 កំពុង Upload...", message.chat.id, msg.message_id)

    upload_ok = False
    for attempt in range(3):
        try:
            with open(file_path, "rb") as video:
                bot.send_video(
                    message.chat.id,
                    video,
                    timeout=120,
                    supports_streaming=True,
                    caption=f"✅ {size_mb:.1f} MB | {platform.upper()} | MP4"
                )
            upload_ok = True
            break
        except Exception as upload_err:
            if attempt == 2:
                bot.edit_message_text(
                    f"❌ Upload បរាជ័យ: {str(upload_err)[:150]}",
                    message.chat.id, msg.message_id
                )
                log_download(user, url, f"upload error: {str(upload_err)[:80]}", platform, size_mb)
            else:
                bot.edit_message_text(
                    f"⚠️ Upload ម្ដង​ទៀត {attempt + 1}/3...",
                    message.chat.id, msg.message_id
                )

    if upload_ok:
        bot.edit_message_text("✅ រួច​រាល់!", message.chat.id, msg.message_id)
        log_download(user, url, "success", platform, size_mb)

    if file_path and os.path.exists(file_path):
        os.remove(file_path)


print("Bot is running...")
bot.infinity_polling()