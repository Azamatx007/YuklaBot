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
ADMIN_ID = 6698039974  
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

# --- MAJBURIY OBUNA ---
async def is_subscribed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_settings()
    if settings['force_sub'] == 0:
        return True
    
    try:
        user_id = update.effective_user.id
        if user_id == ADMIN_ID: return True # Adminni tekshirmaslik

        ch_id = settings['channel_id']
        member = await context.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        return member.status not in ['left', 'kicked']
    except Exception as e:
        logger.warning(f"Obuna xatosi: {e}")
        return True 

# --- ADMIN PANEL TUGMALARI ---
def admin_keyboard():
    settings = get_settings()
    sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📊 Statistika", callback_data="admin_stat")],
        [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
        [InlineKeyboardButton("📢 Kanalni o'zgartirish", callback_data="edit_channel")],
        [InlineKeyboardButton("📝 Reklama (Qo'llanma)", callback_data="send_help")]
    ])

# --- BOT LOGIKASI ---
class InstagramDownloader:
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, ?)", (user_id, 'active'))
        conn.commit()
        conn.close()

        if not await is_subscribed(update, context):
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

        await update.message.reply_text("💎 <b>Tayyor!</b> Instagram linkini yuboring:")

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        await update.message.reply_text("🕹 <b>Admin boshqaruv paneli:</b>", 
                                       reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        
        # Tugma bosilganda darrov "loading" holatini olib tashlash
        await query.answer()

        if query.data == "check_sub":
            if await is_subscribed(update, context):
                await query.message.delete()
                await context.bot.send_message(user_id, "✅ Rahmat! Endi botdan foydalanishingiz mumkin.")
            else:
                await query.answer("❌ Siz hali kanalga a'zo emassiz!", show_alert=True)
            return

        # Faqat admin uchun qolgan tugmalar
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
            await query.message.edit_text("🚀 <b>Reklama yuborish uchun:</b>\n\nXabarni yozing va unga reply qilib <code>/send</code> buyrug'ini yuboring.", 
                                         reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

        elif query.data == "cancel":
            context.user_data['step'] = None
            await query.message.edit_text("🕹 Admin paneli:", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Reklama yuborish uchun xabarga reply qilib <code>/send</code> yozing.")
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
        
        await status_msg.edit_text(f"✅ <b>Tugatildi!</b>\n\n🟢 Yetkazildi: {sent}\n🔴 O'chirib yuborganlar: {failed}", parse_mode=ParseMode.HTML)

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text
        step = context.user_data.get('step')

        # Admin kanalni o'zgartirayotgan bo'lsa
        if user_id == ADMIN_ID and step == 'change_ch':
            conn = get_db_connection()
            conn.execute("UPDATE settings SET channel_id = ? WHERE id = 1", (text,))
            conn.commit()
            conn.close()
            context.user_data['step'] = None
            await update.message.reply_text(f"✅ Kanal muvaffaqiyatli saqlandi: {text}", reply_markup=admin_keyboard())
            return

        # Obunani tekshirish (faqat oddiy foydalanuvchilar va link yuborilganda)
        if "instagram.com" in text:
            if not await is_subscribed(update, context):
                await self.start(update, context)
                return
            
            status_msg = await update.message.reply_text("⚡️ <b>Tahlil qilinmoqda...</b>", parse_mode=ParseMode.HTML)
            file_id = str(uuid.uuid4())
            file_template = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

            ydl_opts = {
                'format': 'bestvideo+bestaudio/best',
                'outtmpl': file_template,
                'ffmpeg_location': FFMPEG_PATH,
                'cookiefile': INSTAGRAM_COOKIES if os.path.exists(INSTAGRAM_COOKIES) else None,
                'merge_output_format': 'mp4',
                'quiet': True
            }

            try:
                await status_msg.edit_text("⏳ <b>Yuklab olinmoqda...</b>", parse_mode=ParseMode.HTML)
                loop = asyncio.get_running_loop()
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = await loop.run_in_executor(None, lambda: ydl.extract_info(text, download=True))
                    downloaded_file = ydl.prepare_filename(info)
                    
                    # Agar fayl nomi o'zgarsa (mp4 ga merge bo'lsa)
                    if not os.path.exists(downloaded_file):
                        ext = info.get('ext', 'mp4')
                        downloaded_file = downloaded_file.rsplit('.', 1)[0] + "." + ext

                await status_msg.edit_text("📤 <b>Yuborilmoqda...</b>", parse_mode=ParseMode.HTML)
                with open(downloaded_file, 'rb') as video:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id, 
                        video=video,
                        caption=f"🎬 <b>@GoYuklaBot orqali yuklandi</b>\n\n📥 {text.split('?')[0]}",
                        parse_mode=ParseMode.HTML
                    )
                await status_msg.delete()
            except Exception as e:
                logger.error(f"Download Error: {e}")
                await status_msg.edit_text("❌ Xatolik yuz berdi. Link noto'g'ri yoki video yopiq.")
            finally:
                # Faylni tozalash
                if 'downloaded_file' in locals() and os.path.exists(downloaded_file):
                    try: os.remove(downloaded_file)
                    except: pass
        else:
            # Agar oddiy matn bo'lsa va obuna bo'lmagan bo'lsa
            if not await is_subscribed(update, context):
                await self.start(update, context)

# --- RUNNER ---
if __name__ == "__main__":
    bot_logic = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("admin", bot_logic.admin_panel))
    app.add_handler(CommandHandler("send", bot_logic.broadcast_send))
    
    # CallbackQueryHandler ni to'g'ri bog'lash
    app.add_handler(CallbackQueryHandler(bot_logic.callback_handler))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_text))

    print("🚀 Bot ishga tushdi!")
    app.run_polling()
