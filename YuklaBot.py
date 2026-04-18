import imageio_ffmpeg
FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
import os
import asyncio
import logging
import sqlite3
import yt_dlp
import uuid
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, 
    filters, ContextTypes
)
from telegram.constants import ParseMode
from telegram.error import Forbidden, BadRequest

# --- SOZLAMALAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6698039974  
CHANNEL_ID = "@MafiaRoyale2"  
DOWNLOAD_DIR = "downloads"
INSTAGRAM_COOKIES = "instagram_cookies.txt"

# FFmpeg yo'li (Agar Windowsda bo'lsangiz va Pathga qo'shmagan bo'lsangiz, bu yerga yo'lini yozing)
FFMPEG_PATH = "ffmpeg" 

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- BAZA BILAN ISHLASH ---
def get_db_connection():
    conn = sqlite3.connect("users.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')")
    conn.commit()
    conn.close()

init_db()

# --- MAJBURIY OBUNA ---
async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(chat_id=CHANNEL_ID, user_id=update.effective_user.id)
        return member.status not in ['left', 'kicked']
    except Exception as e:
        logger.warning(f"Obuna tekshirishda xato (Bot admin bo'lmasligi mumkin): {e}")
        return True 

# --- ASOSIY LOGIKA ---
class InstagramDownloader:
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, ?)", (user_id, 'active'))
        conn.commit()
        conn.close()

        if not await is_subscribed(update, context):
            keyboard = [[InlineKeyboardButton("A'zo bo'lish ✅", url=f"https://t.me/{CHANNEL_ID[1:]}")]]
            await update.message.reply_text(
                f"👋 <b>Assalomu alaykum!</b>\n\nBotdan foydalanish uchun rasmiy kanalimizga a'zo bo'ling:",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
            return

        await update.message.reply_text(
            "💎 <b>Instagram Downloader ishga tushdi!</b>\n\nMedia yuklash uchun link yuboring:",
            parse_mode=ParseMode.HTML
        )

    # --- ADMIN FUNKSIYALARI ---
    async def stat(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        
        conn = get_db_connection()
        active = conn.execute("SELECT COUNT(*) FROM users WHERE status = 'active'").fetchone()[0]
        blocked = conn.execute("SELECT COUNT(*) FROM users WHERE status = 'blocked'").fetchone()[0]
        conn.close()
        
        await update.message.reply_text(
            f"📊 <b>Bot statistikasi:</b>\n\n👤 Faol: {active}\n🚫 Blok: {blocked}\n\n"
            f"<b>Jami: {active + blocked}</b>", parse_mode=ParseMode.HTML
        )

    async def broadcast(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        if not update.message.reply_to_message:
            await update.message.reply_text("📣 Reklama uchun xabarga reply qilib <code>/send</code> yozing.")
            return

        status_msg = await update.message.reply_text("🚀 Reklama yuborilmoqda...")
        conn = get_db_connection()
        users = conn.execute("SELECT user_id FROM users").fetchall()
        
        sent, blocked_count = 0, 0
        for user in users:
            try:
                await context.bot.copy_message(
                    chat_id=user['user_id'], 
                    from_chat_id=update.effective_chat.id, 
                    message_id=update.message.reply_to_message.message_id
                )
                sent += 1
                await asyncio.sleep(0.05)
            except (Forbidden, BadRequest):
                conn.execute("UPDATE users SET status = 'blocked' WHERE user_id = ?", (user['user_id'],))
                blocked_count += 1
        
        conn.commit()
        conn.close()
        await status_msg.edit_text(f"✅ Tugatildi!\n\n✅ Yetkazildi: {sent}\n🚫 Bloklaganlar: {blocked_count}")

    # --- YUKLASH TIZIMI ---
    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        url = update.message.text.strip()
        
        if not await is_subscribed(update, context):
            await self.start(update, context)
            return

        if "instagram.com" not in url:
            await update.message.reply_text("⚠️ Iltimos, faqat <b>Instagram</b> linkini yuboring!")
            return

        status_msg = await update.message.reply_text("⚡️ <b>Tahlil qilinmoqda...</b>", parse_mode=ParseMode.HTML)
        file_id = str(uuid.uuid4())
        file_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': file_path,
            'quiet': True,
            'no_warnings': True,
            'ffmpeg_location': FFMPEG_PATH,
            'cookiefile': INSTAGRAM_COOKIES if os.path.exists(INSTAGRAM_COOKIES) else None,
            'merge_output_format': 'mp4',
        }

        try:
            await status_msg.edit_text("⏳ <b>Yuklab olinmoqda...</b>", parse_mode=ParseMode.HTML)
            
            loop = asyncio.get_running_loop()
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await loop.run_in_executor(None, lambda: ydl.extract_info(url, download=True))
                actual_filename = ydl.prepare_filename(info).replace(".%(ext)s", ".mp4")
                # Ba'zan extension o'zgarib ketishi mumkin
                if not os.path.exists(actual_filename):
                    actual_filename = actual_filename.rsplit('.', 1)[0] + ".mp4"

            await status_msg.edit_text("📤 <b>Yuborilmoqda...</b>", parse_mode=ParseMode.HTML)
            
            with open(actual_filename, 'rb') as video:
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=video,
                    caption=f"🎬 <b>@GoYuklaBot orqali yuklandi</b>\n\n📥 {url.split('?')[0]}",
                    parse_mode=ParseMode.HTML
                )
            await status_msg.delete()

        except Exception as e:
            logger.error(f"Xatolik: {e}")
            error_text = "❌ <b>Xatolik yuz berdi!</b>\n"
            if "ffmpeg" in str(e).lower():
                error_text += "Serverda FFmpeg o'rnatilmagan."
            else:
                error_text += "Video yopiq profildan bo'lishi yoki link xato bo'lishi mumkin."
            await status_msg.edit_text(error_text, parse_mode=ParseMode.HTML)
        
        finally:
            # Fayllarni tozalash
            for f in os.listdir(DOWNLOAD_DIR):
                if file_id in f:
                    try: os.remove(os.path.join(DOWNLOAD_DIR, f))
                    except: pass

# --- RUNNER ---
if __name__ == "__main__":
    bot_logic = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("stat", bot_logic.stat))
    app.add_handler(CommandHandler("send", bot_logic.broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_message))

    print("🚀 Bot muvaffaqiyatli ishga tushdi!")
    app.run_polling()
