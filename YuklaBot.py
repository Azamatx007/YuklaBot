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

# --- LOGGING ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- SOZLAMALAR ---
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6698039974  # Sizning ID raqamingiz
DOWNLOAD_DIR = "downloads"
INSTAGRAM_COOKIES = "instagram_cookies.txt"

try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# --- BAZA BILAN ISHLASH ---
def get_db_connection():
    conn = sqlite3.connect("users.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, channel_id TEXT, force_sub INTEGER DEFAULT 0)")
    
    check = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
    if check == 0:
        conn.execute("INSERT INTO settings (id, channel_id, force_sub) VALUES (1, '@MafiaRoyale2', 0)")
    
    conn.commit()
    conn.close()

init_db()

def get_settings():
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return row

# --- ADMIN PANEL TUGMALARI ---
def admin_keyboard():
    settings = get_settings()
    sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📊 Statistika", callback_data="admin_stat")],
        [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
        [InlineKeyboardButton("📢 Kanalni o'zgartirish", callback_data="edit_channel")],
        [InlineKeyboardButton("📝 Reklama yuborish", callback_data="send_help")]
    ])

# --- MAJBURIY OBUNA TEKSHIRISH ---
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if settings['force_sub'] == 0:
        return True
    
    user_id = update.effective_user.id
    if user_id == ADMIN_ID:
        return True

    try:
        ch_id = settings['channel_id']
        member = await context.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except Exception as e:
        logger.error(f"Obuna tekshirishda xato: {e}")
        return True

# --- ASOSIY KLASS ---
class InstagramDownloader:
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        # Foydalanuvchini bazaga qo'shish
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, 'active')", (user_id,))
        conn.commit()
        conn.close()

        if not await check_subscription(update, context):
            settings = get_settings()
            ch = settings['channel_id']
            link = f"https://t.me/{ch[1:]}" if ch.startswith('@') else f"https://t.me/{ch}"
            
            keyboard = [[InlineKeyboardButton("Kanalga a'zo bo'lish ✅", url=link)],
                        [InlineKeyboardButton("Tekshirish 🔄", callback_data="check_sub")]]
            
            await update.message.reply_text(
                "👋 <b>Botdan foydalanish uchun kanalimizga a'zo bo'ling!</b>",
                reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML
            )
            return

        await update.message.reply_text("💎 <b>Xush kelibsiz!</b>\n\nInstagram video linkini yuboring:")

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: 
            return
        await update.message.reply_text("🕹 <b>Admin boshqaruv paneli:</b>", 
                                       reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()

        if query.data == "check_sub":
            if await check_subscription(update, context):
                await query.message.delete()
                await context.bot.send_message(user_id, "✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
            else:
                await query.answer("❌ Siz hali kanalga a'zo emassiz!", show_alert=True)
            return

        # ADMIN FUNKSIYALARI
        if user_id != ADMIN_ID: return

        if query.data == "admin_stat":
            conn = get_db_connection()
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            conn.close()
            await query.message.edit_text(f"📊 <b>Bot foydalanuvchilari:</b> {count} ta", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

        elif query.data == "toggle_sub":
            conn = get_db_connection()
            conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
            conn.commit()
            conn.close()
            await query.message.edit_reply_markup(reply_markup=admin_keyboard())

        elif query.data == "edit_channel":
            context.user_data['step'] = 'change_ch'
            await query.message.edit_text("📝 Yangi kanal yuzernomini yuboring (Masalan: @MafiaRoyale2):", 
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Bekor qilish", callback_data="cancel")]]))

        elif query.data == "send_help":
            await query.message.edit_text("🚀 <b>Reklama yuborish uchun:</b>\n\nXabarni (rasm, video yoki matn) yozing va unga reply qilib <code>/send</code> buyrug'ini yuboring.", 
                                         reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

        elif query.data == "cancel":
            context.user_data['step'] = None
            await query.message.edit_text("🕹 Admin paneli:", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reklama yuborish uchun biror xabarga reply qilib /send deb yozing.")
            return

        status_msg = await update.message.reply_text("🚀 Reklama tarqatilmoqda...")
        conn = get_db_connection()
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()

        sent, failed = 0, 0
        for user in users:
            try:
                await context.bot.copy_message(
                    chat_id=user['user_id'],
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.reply_to_message.message_id
                )
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        
        await status_msg.edit_text(f"✅ <b>Tugatildi!</b>\n\n🟢 Yetkazildi: {sent}\n🔴 Bloklaganlar: {failed}", parse_mode=ParseMode.HTML)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        step = context.user_data.get('step')

        # Kanalni o'zgartirish jarayoni
        if user_id == ADMIN_ID and step == 'change_ch':
            if text.startswith('@'):
                conn = get_db_connection()
                conn.execute("UPDATE settings SET channel_id = ? WHERE id = 1", (text,))
                conn.commit()
                conn.close()
                context.user_data['step'] = None
                await update.message.reply_text(f"✅ Kanal saqlandi: {text}", reply_markup=admin_keyboard())
            else:
                await update.message.reply_text("❌ Xato! Kanal yuzernomi @ bilan boshlanishi kerak.")
            return

        # Instagram yuklash jarayoni
        if "instagram.com" in text:
            if not await check_subscription(update, context):
                await self.start(update, context)
                return
            
            status_msg = await update.message.reply_text("⚡️ <b>Tahlil qilinmoqda...</b>", parse_mode=ParseMode.HTML)
            file_id = str(uuid.uuid4())
            file_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")

            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': file_path,
                'ffmpeg_location': FFMPEG_PATH,
                'cookiefile': INSTAGRAM_COOKIES if os.path.exists(INSTAGRAM_COOKIES) else None,
                'quiet': True,
                'no_warnings': True,
            }

            try:
                await status_msg.edit_text("⏳ <b>Yuklab olinmoqda...</b>", parse_mode=ParseMode.HTML)
                loop = asyncio.get_running_loop()
                
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    # Yuklashni executor ichida bajarish (bot qotib qolmasligi uchun)
                    await loop.run_in_executor(None, lambda: ydl.download([text]))

                if os.path.exists(file_path):
                    await status_msg.edit_text("📤 <b>Yuborilmoqda...</b>", parse_mode=ParseMode.HTML)
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=open(file_path, 'rb'),
                        caption=f"🎬 <b>@GoYuklaBot orqali yuklandi</b>\n\n📥 {text.split('?')[0]}",
                        parse_mode=ParseMode.HTML
                    )
                    await status_msg.delete()
                else:
                    await status_msg.edit_text("❌ Video yuklanmadi. Link noto'g'ri yoki profil yopiq.")

            except Exception as e:
                logger.error(f"Download Error: {e}")
                await status_msg.edit_text("❌ Xatolik: Video topilmadi yoki yuklash imkonsiz.")
            
            finally:
                if os.path.exists(file_path):
                    os.remove(file_path)
        else:
            if user_id != ADMIN_ID:
                await update.message.reply_text("🔍 Iltimos, Instagram video linkini yuboring.")

# --- RUNNER ---
if __name__ == "__main__":
    bot_logic = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()

    # Handlerlarni ro'yxatga olish
    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("admin", bot_logic.admin_panel))
    app.add_handler(CommandHandler("send", bot_logic.broadcast_send))
    app.add_handler(CallbackQueryHandler(bot_logic.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_text))

    print("🚀 Bot muvaffaqiyatli ishga tushdi!")
    app.run_polling()
