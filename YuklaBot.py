import os
import asyncio
import logging
import traceback
import yt_dlp
import uuid
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from telegram.constants import ParseMode

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
DOWNLOAD_DIR = "downloads"
BOT_USERNAME = "@GoYuklaBot"

INSTAGRAM_COOKIES = "instagram_cookies.txt"
YOUTUBE_COOKIES = "youtube_cookies1.txt"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)


class UI:
    WELCOME = (
        "<b>👋 Assalomu alaykum, {}!</b>\n\n"
        "📥 Video: YouTube, Instagram, TikTok, Facebook\n"
        "🎵 Musiqa: Nomini yozing — 10 ta natija chiqadi\n\n"
        "✨ Link yuboring yoki musiqa nomini yozing:"
    )
    PROCESSING = "⏳ <b>Iltimos, kuting...</b>\n🔄 Yuklab olinmoqda..."
    UPLOADING = "📤 <b>Tayyor!</b>\n✨ Fayl yuborilmoqda..."
    CAPTION_VIDEO = "🎬 <b>{}</b>\n\n✨ {}"
    CAPTION_MUSIC = "🎵 <b>{}</b>\n\n✨ {}"
    ERROR = "❌ <b>Kechirasiz!</b>\n\nKeyinroq urinib ko‘ring yoki boshqa link/nom sinang."
    NOT_FOUND = "🔍 <b>Hech narsa topilmadi.</b>"
    MUSIC_RESULTS = (
        "🔍 <b>\"{query}\"</b> bo‘yicha topilgan natijalar:\n\n"
        "{results_list}\n\n"
        "🎵 <b>1-10 gacha raqam yozing</b> — shu qo‘shiq yuklanadi!"
    )


def get_ydl_opts(file_base: str, is_audio: bool = False, cookies_file: str = None):
    opts = {
        'outtmpl': f'{file_base}.%(ext)s',
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'retries': 10,
        'fragment_retries': 10,
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'geo_bypass': True,
        'noplaylist': True,
        'socket_timeout': 120,
    }

    if cookies_file and os.path.exists(cookies_file):
        opts['cookies'] = cookies_file

    if is_audio:
        # Musiqa → faqat MP3
        opts['format'] = 'bestaudio/best'
        opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }]
    else:
        # Video → faqat MP4 (webm chiqmaydi!)
        opts['format'] = 'bestvideo*[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo*[height<=720]+bestaudio/best'
        opts['merge_output_format'] = 'mp4'          # Bu qator webm → mp4 aylantiradi

    return opts


class ProfessionalDownloader:
    def __init__(self):
        self.platforms = ["youtube.com", "youtu.be", "instagram.com", "tiktok.com",
                         "facebook.com", "fb.watch", "vt.tiktok.com"]

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            UI.WELCOME.format(update.effective_user.first_name),
            parse_mode=ParseMode.HTML
        )

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        if not text:
            return

        user_id = update.effective_user.id

        if text.isdigit() and f"pending_music_{user_id}" in context.user_data:
            await self.process_selected_music(update, context, int(text))
            return

        if any(p in text.lower() for p in self.platforms) or text.startswith(("http://", "https://")):
            await self.process_download(update, context, text)
        else:
            await self.youtube_search(update, context, text)

    # Qidiruv va boshqa funksiyalar o‘zgarmadi (oldingi kod bilan bir xil)
    async def youtube_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
        status_msg = await update.message.reply_text(
            f"🔍 <b>\"{query}\"</b> qidirilmoqda...",
            parse_mode=ParseMode.HTML
        )

        try:
            user_id = update.effective_user.id
            loop = asyncio.get_running_loop()
            ydl_opts = {
                'quiet': True,
                'extract_flat': True,
                'geo_bypass': True,
                'noplaylist': True,
            }
            if os.path.exists(YOUTUBE_COOKIES):
                ydl_opts['cookies'] = YOUTUBE_COOKIES

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(
                    None, lambda: ydl.extract_info(f"ytsearch10:{query}", download=False)
                )

            if not info or 'entries' not in info or not info['entries']:
                await status_msg.edit_text(UI.NOT_FOUND, parse_mode=ParseMode.HTML)
                return

            entries = info['entries'][:10]
            results_list = []
            for i, entry in enumerate(entries, 1):
                title = entry.get('title', 'Noma’lum qo‘shiq')
                uploader = entry.get('uploader', 'Artist')
                duration = entry.get('duration')
                dur_str = "??:??"
                if duration is not None:
                    try:
                        dur = int(float(duration))
                        dur_str = f"{dur//60:02d}:{dur%60:02d}"
                    except:
                        pass
                results_list.append(f"<b>{i}.</b> {title}\n👤 {uploader} | ⏱ {dur_str}")

            await status_msg.edit_text(
                UI.MUSIC_RESULTS.format(query=query, results_list="\n\n".join(results_list)),
                parse_mode=ParseMode.HTML
            )

            context.user_data[f"pending_music_{user_id}"] = {'entries': entries}

        except Exception as e:
            logger.error(f"Qidiruv xatosi: {e}\n{traceback.format_exc()}")
            await status_msg.edit_text(UI.ERROR, parse_mode=ParseMode.HTML)

    async def process_selected_music(self, update: Update, context: ContextTypes.DEFAULT_TYPE, num: int):
        # ... (oldingi kod bilan bir xil, o‘zgartirish yo‘q)
        user_id = update.effective_user.id
        data = context.user_data.get(f"pending_music_{user_id}")
        if not data:
            return

        entries = data['entries']
        if num < 1 or num > len(entries):
            await update.message.reply_text("❌ Noto‘g‘ri raqam! 1-10 oralig‘ida yozing.")
            return

        selected = entries[num - 1]
        url = selected.get('url') or selected.get('webpage_url')
        if not url:
            await update.message.reply_text(UI.ERROR, parse_mode=ParseMode.HTML)
            return

        status_msg = await update.message.reply_text(UI.PROCESSING, parse_mode=ParseMode.HTML)
        file_id = str(uuid.uuid4())
        file_base = os.path.join(DOWNLOAD_DIR, file_id)

        try:
            loop = asyncio.get_running_loop()
            cookies_file = YOUTUBE_COOKIES if os.path.exists(YOUTUBE_COOKIES) else None
            ydl_opts = get_ydl_opts(file_base, is_audio=True, cookies_file=cookies_file)

            info = await loop.run_in_executor(
                None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True)
            )

            title = info.get('title', selected.get('title', 'Musiqa'))[:100]
            performer = info.get('uploader', 'Artist')[:50]

            file_path = self.find_file(file_id)
            if file_path:
                await status_msg.edit_text(UI.UPLOADING, parse_mode=ParseMode.HTML)
                with open(file_path, 'rb') as f:
                    await context.bot.send_audio(
                        chat_id=update.effective_chat.id,
                        audio=f,
                        title=title,
                        performer=performer,
                        caption=UI.CAPTION_MUSIC.format(title, BOT_USERNAME),
                        parse_mode=ParseMode.HTML,
                        read_timeout=120,
                        write_timeout=120
                    )
                await status_msg.delete()
            else:
                await status_msg.edit_text(UI.NOT_FOUND, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error(f"Musiqa yuklash xatosi: {e}\n{traceback.format_exc()}")
            await status_msg.edit_text(UI.ERROR, parse_mode=ParseMode.HTML)
        finally:
            self.cleanup(file_id)
            context.user_data.pop(f"pending_music_{user_id}", None)

    async def process_download(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
        status_msg = await update.message.reply_text(UI.PROCESSING, parse_mode=ParseMode.HTML)
        file_id = str(uuid.uuid4())
        file_base = os.path.join(DOWNLOAD_DIR, file_id)

        cookies_file = INSTAGRAM_COOKIES if "instagram.com" in url.lower() else None

        try:
            loop = asyncio.get_running_loop()
            ydl_opts = get_ydl_opts(file_base, is_audio=False, cookies_file=cookies_file)

            info = await loop.run_in_executor(
                None, lambda: yt_dlp.YoutubeDL(ydl_opts).extract_info(url, download=True)
            )

            title = (info.get('title') or 'Video')[:100]
            file_path = self.find_file(file_id)

            if not file_path:
                raise Exception("Fayl topilmadi")

            await status_msg.edit_text(UI.UPLOADING, parse_mode=ParseMode.HTML)

            with open(file_path, 'rb') as f:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=f,
                    caption=UI.CAPTION_VIDEO.format(title, BOT_USERNAME),
                    parse_mode=ParseMode.HTML,
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120
                )
            await status_msg.delete()

        except Exception as e:
            logger.error(f"Video yuklash xatosi: {e}\n{traceback.format_exc()}")
            await status_msg.edit_text(UI.ERROR, parse_mode=ParseMode.HTML)
        finally:
            self.cleanup(file_id)

    def find_file(self, file_id: str):
        for f in os.listdir(DOWNLOAD_DIR):
            if f.startswith(file_id):
                return os.path.join(DOWNLOAD_DIR, f)
        return None

    def cleanup(self, file_id: str):
        try:
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(file_id):
                    os.remove(os.path.join(DOWNLOAD_DIR, f))
        except:
            pass


if __name__ == "__main__":
    if not TOKEN:
        print("❌ BOT_TOKEN topilmadi! .env faylini tekshiring.")
        exit(1)

    print("🚀 Bot muvaffaqiyatli ishga tushdi!")
    print(f"Instagram cookies: {'Bor ✓' if os.path.exists(INSTAGRAM_COOKIES) else 'Yo‘q'}")
    print(f"YouTube cookies  : {'Bor ✓' if os.path.exists(YOUTUBE_COOKIES) else 'Yo‘q'}")

    bot = ProfessionalDownloader()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_message))

    print("👉 Bot polling rejimida ishlamoqda...")
    app.run_polling()
