import os
import telebot
from yt_dlp import YoutubeDL
from typing import Any
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
API_TOKEN = os.getenv("BOT_TOKEN", "7775236984:AAEG62do6oEDZh40cg9yuqvq5tSC2vC_hKM")
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "-1003738559042")  # e.g. -1001234567890

if not API_TOKEN:
    raise ValueError("BOT_TOKEN not found in .env file!")
if not LOG_CHANNEL_ID:
    raise ValueError("LOG_CHANNEL_ID not found in .env file!")

bot = telebot.TeleBot(API_TOKEN)

DOWNLOAD_FOLDER = "downloads"
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

# ✅ Log to Telegram Channel
def log_to_channel(user, url: str, status: str, size_mb: float = 0.0):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = f"@{user.username}" if user.username else "No username"
    
    emoji = "✅" if status == "success" else "❌"
    
    log_msg = (
        f"{emoji} **Download Log**\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 User: {user.first_name} ({username})\n"
        f"🆔 Chat ID: `{user.id}`\n"
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

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(message,
        "Hello! 👋 Send me a video link and I'll download it!\n\n"
        "✅ Supported:\n"
        "• YouTube • TikTok • Instagram\n"
        "• Facebook • Twitter/X • Reddit\n"
        "• Vimeo • Dailymotion • Twitch\n\n"
        "📽️ Format: MP4 | Max: 50MB"
    )

@bot.message_handler(func=lambda message: True)
def download_video(message):
    url = message.text.strip()
    user = message.from_user

    if not url.startswith("http"):
        bot.reply_to(message, "⚠️ Please send a valid URL starting with http/https")
        return

    msg = bot.send_message(message.chat.id, "⏳ Processing link...")
    file_path: str | None = None
    size_mb: float = 0.0

    try:
        ydl_opts: dict[str, Any] = {
            'outtmpl': f'{DOWNLOAD_FOLDER}/%(title)s.%(ext)s',
            'format': (
                'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/'
                'bestvideo[height<=720]+bestaudio/'
                'best[height<=720]/'
                'best'
            ),
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
            },
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
            'postprocessor_args': [
                '-movflags', 'faststart',
                '-c:v', 'copy',
                '-c:a', 'aac',
            ],
            'retries': 5,
            'fragment_retries': 5,
        }

        bot.edit_message_text("⬇️ Downloading...", message.chat.id, msg.message_id)

        with YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
            info_dict = ydl.extract_info(url, download=True)
            file_path = ydl.prepare_filename(info_dict)
            if not file_path.endswith('.mp4'):
                file_path = os.path.splitext(file_path)[0] + '.mp4'

        file_size = os.path.getsize(file_path)
        size_mb = file_size / (1024 * 1024)

        if file_size > 50 * 1024 * 1024:
            bot.edit_message_text(
                f"❌ Video too large ({size_mb:.1f}MB)\nTelegram limit is 50MB.",
                message.chat.id, msg.message_id
            )
            # ✅ Log failed (too large)
            log_to_channel(user, url, f"❌ Too large ({size_mb:.1f}MB)", size_mb)
            os.remove(file_path)
            return

        bot.edit_message_text("📤 Uploading...", message.chat.id, msg.message_id)

        for attempt in range(3):
            try:
                with open(file_path, 'rb') as video:
                    bot.send_video(
                        message.chat.id,
                        video,
                        timeout=120,
                        supports_streaming=True,
                        caption=f"✅ {size_mb:.1f}MB | 720p MP4"
                    )
                break
            except Exception as upload_err:
                if attempt == 2:
                    raise upload_err
                bot.edit_message_text(
                    f"⚠️ Retry {attempt+1}/3...",
                    message.chat.id, msg.message_id
                )

        bot.edit_message_text("✅ Done!", message.chat.id, msg.message_id)

        # ✅ Log success
        log_to_channel(user, url, "success", size_mb)
        os.remove(file_path)

    except Exception as e:
        error_msg = str(e)

        if "Unsupported URL" in error_msg:
            reply = "❌ This site is not supported."
        elif "format is not available" in error_msg:
            reply = "❌ No downloadable format found."
        elif "Private video" in error_msg:
            reply = "❌ This video is private."
        elif "timed out" in error_msg.lower():
            reply = "❌ Connection timed out. Try again."
        else:
            reply = f"❌ Error: {error_msg}"

        bot.edit_message_text(reply, message.chat.id, msg.message_id)

        # ✅ Log error
        log_to_channel(user, url, f"error: {error_msg[:100]}", size_mb)

        if file_path is not None and os.path.exists(file_path):
            os.remove(file_path)

print("Bot is running...")
bot.infinity_polling()