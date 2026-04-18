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
from telegram.error import BadRequest, TelegramError

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6698039974
DOWNLOAD_DIR = "downloads"

try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ====================== DATABASE ======================
def get_db_connection():
    conn = sqlite3.connect("users.db", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    # Eski jadval
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')")
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, channel_id TEXT, force_sub INTEGER DEFAULT 0)")
    
    # YANGI: Ko'p kanal uchun jadval
    conn.execute("""CREATE TABLE IF NOT EXISTS force_channels (
                    id INTEGER PRIMARY KEY,
                    channel_id TEXT UNIQUE NOT NULL)""")
    
    # Agar settings bo'sh bo'lsa default qiymat
    if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        conn.execute("INSERT INTO settings (id, channel_id, force_sub) VALUES (1, '', 1)")
    
    conn.commit()
    conn.close()

init_db()

def get_settings():
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return row

def get_force_channels():
    """Barcha majburiy kanallarni qaytaradi"""
    conn = get_db_connection()
    rows = conn.execute("SELECT channel_id FROM force_channels").fetchall()
    conn.close()
    return [row['channel_id'] for row in rows]

def add_force_channel(channel_id: str):
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO force_channels (channel_id) VALUES (?)", (channel_id.strip(),))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # allaqachon mavjud
    finally:
        conn.close()

def remove_force_channel(channel_id: str):
    conn = get_db_connection()
    conn.execute("DELETE FROM force_channels WHERE channel_id = ?", (channel_id.strip(),))
    conn.commit()
    conn.close()

# ====================== ADMIN KEYBOARD ======================
def admin_keyboard():
    settings = get_settings()
    sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stat")],
        [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
        [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="add_channel")],
        [InlineKeyboardButton("➖ Kanal o'chirish", callback_data="remove_channel")],
        [InlineKeyboardButton("📋 Kanallar ro'yxati", callback_data="list_channels")],
        [InlineKeyboardButton("📢 Reklama yuborish", callback_data="send_help")]
    ])

# ====================== MAJBURIY OBUNA (KO'P KANAL) ======================
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = get_settings()
    if settings['force_sub'] == 0:
        return True

    if update.callback_query:
        user_id = update.callback_query.from_user.id
    else:
        user_id = update.effective_user.id

    if user_id == ADMIN_ID:
        return True

    channels = get_force_channels()
    if not channels:
        return True

    for ch_id in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator', 'restricted']:
                return False
        except Exception as e:
            logger.warning(f"Kanal tekshirishda xato {ch_id}: {e}")
            return False  # xavfsizroq: biror kanal tekshirib bo'lmasa ham rad etamiz

    return True

# ====================== BOT KLASSI ======================
class InstagramDownloader:

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, 'active')", (user_id,))
        conn.commit()
        conn.close()

        if not await check_subscription(update, context):
            channels = get_force_channels()
            if channels:
                text = "👋 <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:</b>\n\n"
                keyboard = []
                for ch in channels:
                    if ch.startswith('@'):
                        link = f"https://t.me/{ch[1:]}"
                        btn_text = f"📢 {ch}"
                    else:
                        link = ch
                        btn_text = "Kanalga o'tish"
                    keyboard.append([InlineKeyboardButton(btn_text, url=link)])
                keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
            else:
                text = "👋 Botdan foydalanish uchun hech qanday kanal talab qilinmayapti."
                keyboard = [[InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")]]

            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return

        await update.message.reply_text(
            "💎 <b>Xush kelibsiz!</b>\n\nInstagram video yoki Reel linkini yuboring:",
            parse_mode=ParseMode.HTML
        )

    # ... admin_panel, callback_handler, broadcast_send, handle_text funksiyalari quyida ...

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        await update.message.reply_text(
            "🕹 <b>Admin boshqaruv paneli:</b>",
            reply_markup=admin_keyboard(),
            parse_mode=ParseMode.HTML
        )

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()

        if query.data == "check_sub":
            if await check_subscription(update, context):
                try:
                    await query.message.delete()
                except:
                    pass
                await context.bot.send_message(
                    chat_id=user_id,
                    text="✅ Rahmat! Endi botdan foydalanishingiz mumkin.\n\nInstagram linkini yuboring."
                )
            else:
                await query.answer("❌ Hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
            return

        if user_id != ADMIN_ID:
            return

        try:
            if query.data == "admin_stat":
                conn = get_db_connection()
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                conn.close()
                await query.message.edit_text(f"📊 Bot foydalanuvchilari: {count} ta", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

            elif query.data == "toggle_sub":
                conn = get_db_connection()
                conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
                conn.commit()
                conn.close()
                await query.message.edit_text("🕹 Admin paneli:", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
                await query.answer("✅ Majburiy obuna holati o'zgartirildi!")

            elif query.data == "add_channel":
                context.user_data['step'] = 'add_force_channel'
                await query.message.edit_text(
                    "➕ Yangi kanal username'ni yuboring (masalan: @KanalNomi):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Bekor qilish", callback_data="cancel")]])
                )

            elif query.data == "remove_channel":
                context.user_data['step'] = 'remove_force_channel'
                await query.message.edit_text(
                    "➖ O'chirish kerak bo'lgan kanal username'ni yuboring:",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Bekor qilish", callback_data="cancel")]])
                )

            elif query.data == "list_channels":
                channels = get_force_channels()
                if channels:
                    txt = "📋 <b>Majburiy kanallar ro'yxati:</b>\n\n" + "\n".join([f"• {ch}" for ch in channels])
                else:
                    txt = "📋 Hozircha majburiy kanal yo'q."
                await query.message.edit_text(txt, reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

            elif query.data == "cancel":
                context.user_data['step'] = None
                await query.message.edit_text("🕹 Admin paneli:", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error(f"Callback xatosi: {e}")

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ... o'zgarmadi (oldingi kod bilan bir xil) ...
        if update.effective_user.id != ADMIN_ID:
            return
        if not update.message.reply_to_message:
            await update.message.reply_text("Xabarga reply qilib /send yozing.")
            return
        status = await update.message.reply_text("Reklama yuborilmoqda...")
        conn = get_db_connection()
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        sent = failed = 0
        for u in users:
            try:
                await context.bot.copy_message(u['user_id'], update.effective_chat.id, update.message.reply_to_message.message_id)
                sent += 1
                await asyncio.sleep(0.05)
            except:
                failed += 1
        await status.edit_text(f"Tugatildi!\nYetkazildi: {sent}\nBloklaganlar: {failed}")

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')

        # Kanal qo'shish
        if user_id == ADMIN_ID and step == 'add_force_channel':
            if add_force_channel(text):
                await update.message.reply_text(f"✅ Kanal qo'shildi: {text}", reply_markup=admin_keyboard())
            else:
                await update.message.reply_text("⚠️ Bu kanal allaqachon ro'yxatda bor.")
            context.user_data['step'] = None
            return

        # Kanal o'chirish
        if user_id == ADMIN_ID and step == 'remove_force_channel':
            remove_force_channel(text)
            await update.message.reply_text(f"✅ Kanal o'chirildi: {text}", reply_markup=admin_keyboard())
            context.user_data['step'] = None
            return

        # Instagram yuklash qismi (oldingi kodingiz bilan bir xil)
        if "instagram.com" in text:
            if not await check_subscription(update, context):
                await self.start(update, context)
                return
            # ... Instagram yuklash kodingiz shu yerda qoladi (avvalgi kod bilan bir xil) ...
            status_msg = await update.message.reply_text("⚡️ Tahlil qilinmoqda...")
            file_id = str(uuid.uuid4())
            file_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.mp4")
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
                'outtmpl': file_path,
                'merge_output_format': 'mp4',
                'ffmpeg_location': FFMPEG_PATH,
                'quiet': True,
                'noplaylist': True,
            }
            try:
                await status_msg.edit_text("⏳ Yuklab olinmoqda...")
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([text]))
                if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                    await context.bot.send_video(
                        update.effective_chat.id,
                        open(file_path, 'rb'),
                        caption=f"🎬 @GoYuklaBot orqali yuklandi\n{text.split('?')[0]}",
                        parse_mode=ParseMode.HTML
                    )
                    await status_msg.delete()
                else:
                    await status_msg.edit_text("❌ Yuklab bo'lmadi.")
            except Exception as e:
                logger.error(f"Download error: {e}")
                await status_msg.edit_text("❌ Xatolik yuz berdi.")
            finally:
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except:
                        pass
        else:
            if user_id != ADMIN_ID:
                await update.message.reply_text("Instagram linkini yuboring.")

# ====================== RUN ======================
if __name__ == "__main__":
    bot_logic = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("admin", bot_logic.admin_panel))
    app.add_handler(CommandHandler("send", bot_logic.broadcast_send))
    app.add_handler(CallbackQueryHandler(bot_logic.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_text))

    print("🚀 Bot muvaffaqiyatli ishga tushdi! (Ko'p kanal rejimi)")
    app.run_polling(drop_pending_updates=True)
