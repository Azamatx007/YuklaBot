import os
import asyncio
import logging
import sqlite3
import json
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
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active')")
    conn.execute("""CREATE TABLE IF NOT EXISTS settings 
                    (id INTEGER PRIMARY KEY, 
                     channels TEXT DEFAULT '[]', 
                     force_sub INTEGER DEFAULT 0)""")
    
    # Eski bitta kanalni yangi formatga o'tkazish
    row = conn.execute("SELECT channels FROM settings WHERE id = 1").fetchone()
    if row and row['channels'] == '[]':
        old_ch = conn.execute("SELECT channel_id FROM settings WHERE id = 1").fetchone()
        if old_ch and old_ch['channel_id']:
            try:
                channels_list = [old_ch['channel_id'].strip()]
                conn.execute("UPDATE settings SET channels = ? WHERE id = 1", 
                            (json.dumps(channels_list),))
            except:
                pass
    
    if conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0:
        conn.execute("INSERT INTO settings (id, channels, force_sub) VALUES (1, '[]', 0)")
    
    conn.commit()
    conn.close()

init_db()

def get_settings():
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM settings WHERE id = 1").fetchone()
    conn.close()
    return row

def get_channels():
    settings = get_settings()
    try:
        return json.loads(settings['channels']) if settings['channels'] else []
    except:
        return []

# ====================== ADMIN KEYBOARD ======================
def admin_keyboard():
    settings = get_settings()
    sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
    channels = get_channels()
    ch_text = f"Kanallar: {len(channels)} ta" if channels else "Kanallar: yo'q"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistika", callback_data="admin_stat")],
        [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
        [InlineKeyboardButton("📢 Kanallarni o'zgartirish", callback_data="edit_channels")],
        [InlineKeyboardButton("📝 Reklama yuborish", callback_data="send_help")]
    ])

# ====================== MAJBURIY OBUNA (KO'P KANAL) ======================
async def check_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    settings = get_settings()
    if settings['force_sub'] == 0:
        return True

    if update.callback_query:
        user_id = update.callback_query.from_user.id
    else:
        user_id = update.effective_user.id if update.effective_user else None

    if not user_id or user_id == ADMIN_ID:
        return True

    channels = get_channels()
    if not channels:
        return True

    not_joined = []
    for ch_id in channels:
        try:
            member = await context.bot.get_chat_member(chat_id=ch_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator', 'restricted']:
                not_joined.append(ch_id)
        except Exception as e:
            logger.warning(f"Kanal tekshirish xatosi ({ch_id}): {e}")
            not_joined.append(ch_id)  # Xato bo'lsa ham majburiy deb hisoblaymiz

    return len(not_joined) == 0

# ====================== BOT KLASSI ======================
class InstagramDownloader:
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        conn = get_db_connection()
        conn.execute("INSERT OR REPLACE INTO users (user_id, status) VALUES (?, 'active')", (user_id,))
        conn.commit()
        conn.close()

        if not await check_subscription(update, context):
            channels = get_channels()
            keyboard = []
            
            for ch in channels:
                link = f"https://t.me/{ch[1:]}" if ch.startswith('@') else ch
                keyboard.append([InlineKeyboardButton(f"📢 {ch}", url=link)])
            
            keyboard.append([InlineKeyboardButton("✅ Barchasini tekshirish", callback_data="check_sub")])

            await update.message.reply_text(
                "👋 <b>Botdan foydalanish uchun barcha kanallarga a'zo bo'ling!</b>\n\n"
                "Quyidagi kanallarga a'zo bo'ling va «Barchasini tekshirish» tugmasini bosing.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return

        await update.message.reply_text(
            "💎 <b>Xush kelibsiz!</b>\n\nInstagram video yoki Reel linkini yuboring:",
            parse_mode=ParseMode.HTML
        )

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
                await query.answer("❌ Siz hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
            return

        if user_id != ADMIN_ID:
            return

        try:
            if query.data == "admin_stat":
                conn = get_db_connection()
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                conn.close()
                await query.message.edit_text(f"📊 Bot foydalanuvchilari: {count} ta", 
                                             reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

            elif query.data == "toggle_sub":
                conn = get_db_connection()
                conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
                conn.commit()
                conn.close()
                await query.message.edit_text("🕹 Admin paneli:", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
                await query.answer("✅ Majburiy obuna holati o'zgartirildi!")

            elif query.data == "edit_channels":
                context.user_data['step'] = 'change_channels'
                current = ", ".join(get_channels()) or "Hozircha yo'q"
                await query.message.edit_text(
                    f"📝 Joriy kanallar: <code>{current}</code>\n\n"
                    "Yangi kanallarni vergul bilan ajratib yuboring:\n"
                    "Masalan: @Kanal1, @Kanal2, @Kanal3",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Bekor qilish", callback_data="cancel")]]),
                    parse_mode=ParseMode.HTML
                )

            elif query.data == "send_help":
                await query.message.edit_text("Reklama yuborish uchun xabarga reply qilib /send yozing.", 
                                             reply_markup=admin_keyboard())

            elif query.data == "cancel":
                context.user_data['step'] = None
                await query.message.edit_text("🕹 Admin paneli:", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Callback xatosi: {e}")

    # broadcast_send va handle_text funksiyalari o'zgarmaydi (oldingi versiyangizdagidek qoldiring)

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ... oldingi kodingizdagidek ...
        pass

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')

        if user_id == ADMIN_ID and step == 'change_channels':
            channels = [ch.strip() for ch in text.split(',') if ch.strip()]
            conn = get_db_connection()
            conn.execute("UPDATE settings SET channels = ? WHERE id = 1", (json.dumps(channels),))
            conn.commit()
            conn.close()
            context.user_data['step'] = None
            await update.message.reply_text(
                f"✅ {len(channels)} ta kanal saqlandi:\n" + "\n".join(channels),
                reply_markup=admin_keyboard(),
                parse_mode=ParseMode.HTML
            )
            return

        # Instagram yuklash qismi (oldingidek)
        if "instagram.com" in text:
            if not await check_subscription(update, context):
                await self.start(update, context)
                return
            # ... yuklash kodingizni shu yerga qo'ying ...
            # (status_msg, yt_dlp va h.k.)
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
