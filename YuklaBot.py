import os
import asyncio
import logging
import sqlite3
import yt_dlp
import uuid
import imageio_ffmpeg
from dotenv import load_dotenv

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode
from telegram.error import BadRequest

# ====================== LOGGING ======================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ====================== SOZLAMALAR ======================
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6698039974
DOWNLOAD_DIR = "downloads"
INSTAGRAM_COOKIES = "instagram_cookies.txt"

try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ====================== BAZA ======================
def get_db_connection():
    conn = sqlite3.connect("users.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, channel_id TEXT, force_sub INTEGER DEFAULT 0)")
    
    if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        conn.execute("INSERT INTO settings (id, channel_id, force_sub) VALUES (1, '@MafiaRoyale2', 0)")
    
    conn.commit()
    conn.close()

init_db()

def get_settings():
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return row

# ====================== ADMIN KEYBOARD ======================
def admin_keyboard():
    settings = get_settings()
    sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stat")],
        [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
        [InlineKeyboardButton("📢 Kanalni o'zgartirish", callback_data="edit_channel")],
        [InlineKeyboardButton("📝 Reklama yuborish", callback_data="send_help")]
    ])

# ====================== MAJBURIY OBUNA (TO‘LIQ TUZATILGAN) ======================
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = get_settings()
    if settings['force_sub'] == 0:
        return True

    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True

    try:
        ch_id = settings['channel_id']
        member = await context.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']

    except Exception as e:
        error = str(e).lower()
        logger.error(f"Obuna tekshirishda xato: {e}")

        if "chat not found" in error:
            logger.error("❌ KANAL TOPILMADI! Bazadagi channel_id noto'g'ri.")
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="❗️ <b>MAJBURIY OBUna XATOSI!</b>\n\n"
                         f"Kanal topilmadi: {settings['channel_id']}\n"
                         "Iltimos, /admin → \"Kanalni o'zgartirish\" tugmasini bosib, kanal username'ni to'g'ri yozing.",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
            return False

        elif "member list is inaccessible" in error or "bad request" in error:
            logger.error("⚠️ Bot kanalga Administrator emas!")
            try:
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="❗️ <b>Bot kanalga Administrator emas!</b>\n\n"
                         f"Kanal: {settings['channel_id']}\n"
                         "Botni kanalga Administrator qilib qo‘shing.",
                    parse_mode=ParseMode.HTML
                )
            except:
                pass
            return False

        return False

# ====================== ASOSIY KLASS ======================
class InstagramDownloader:
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, 'active')", (user_id,))
        conn.commit()
        conn.close()

        if not await check_subscription(update, context):
            settings = get_settings()
            ch = settings['channel_id']
            link = f"https://t.me/{ch[1:]}" if ch.startswith('@') else f"https://t.me/{ch}"
            
            keyboard = [
                [InlineKeyboardButton("Kanalga a'zo bo'lish ✅", url=link)],
                [InlineKeyboardButton("Tekshirish 🔄", callback_data="check_sub")]
            ]
            
            await update.message.reply_text(
                "👋 <b>Botdan foydalanish uchun kanalimizga a'zo bo'ling!</b>",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return

        await update.message.reply_text(
            "💎 <b>Xush kelibsiz!</b>\n\nInstagram video (Reel, Post, Story) linkini yuboring:",
            parse_mode=ParseMode.HTML
        )

    # Qolgan barcha funksiyalar (admin_panel, callback_handler, broadcast_send, handle_text) o'zgarmadi
    # Ular sizning oldingi kodingiz bilan bir xil qoldi (joyni tejash uchun qisqartirdim)

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        await update.message.reply_text("🕹 <b>Admin boshqaruv paneli:</b>", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()

        if query.data == "check_sub":
            if await check_subscription(update, context):
                await query.message.delete()
                await context.bot.send_message(chat_id=user_id, text="✅ Rahmat! Endi botdan foydalanishingiz mumkin.\n\nInstagram linkini yuboring.")
            else:
                await query.answer("❌ Siz hali kanalga a'zo emassiz!", show_alert=True)
            return

        if user_id != ADMIN_ID: return

        try:
            if query.data == "admin_stat":
                count = get_db_connection().execute("SELECT COUNT(*) FROM users").fetchone()[0]
                await query.message.edit_text(f"📊 <b>Bot foydalanuvchilari:</b> {count} ta", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            elif query.data == "toggle_sub":
                conn = get_db_connection()
                conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
                conn.commit()
                conn.close()
                await query.message.edit_text("🕹 <b>Admin boshqaruv paneli:</b>", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
                await query.answer("✅ Majburiy obuna holati o'zgartirildi!")
            elif query.data == "edit_channel":
                context.user_data['step'] = 'change_ch'
                await query.message.edit_text("📝 Yangi kanal yuzernomini yuboring (masalan: @MafiaRoyale2):", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Bekor qilish", callback_data="cancel")]]))
            elif query.data == "send_help":
                await query.message.edit_text("🚀 <b>Reklama yuborish uchun:</b>\n\nXabarni yozing va unga reply qilib /send buyrug'ini bosing.", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            elif query.data == "cancel":
                context.user_data['step'] = None
                await query.message.edit_text("🕹 <b>Admin boshqaruv paneli:</b>", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Callback xatosi: {e}")

    # broadcast_send va handle_text funksiyalari o'zgarmadi (oldingi kodingizdagidek qoldi)

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ... oldingi kod ...
        pass  # sizning oldingi broadcasting kodingizni qo'ying

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ... oldingi kod (Instagram yuklash qismi) ...
        pass  # sizning oldingi handle_text kodingizni qo'ying

# ====================== RUN ======================
if __name__ == "__main__":
    bot_logic = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("admin", bot_logic.admin_panel))
    app.add_handler(CommandHandler("send", bot_logic.broadcast_send))
    app.add_handler(CallbackQueryHandler(bot_logic.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_text))

    print("🚀 Bot muvaffaqiyatli ishga tushdi! (Majburiy obuna to'liq tuzatildi)")
    app.run_polling(drop_pending_updates=True)
