import os
import asyncio
import logging
import sqlite3
import yt_dlp
import uuid
import imageio_ffmpeg
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6698039974
DOWNLOAD_DIR = "downloads"
BOT_NAME = "🌟 Zo'r Ekan Bot"

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
    conn.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, channel_id TEXT, force_sub INTEGER DEFAULT 0)")
    
    conn.execute("""CREATE TABLE IF NOT EXISTS force_channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE NOT NULL)""")
    
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
        return False
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

# ====================== MAJBURIY OBUNA TEKSHIRISH ======================

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
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logger.warning(f"Kanal tekshirishda xato {ch_id}: {e}")
            return False
    
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
            text = "👋 <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:</b>\n\n"
            keyboard = []
            
            for ch in channels:
                if ch.startswith('@'):
                    link = f"https://t.me/{ch[1:]}"
                    btn_text = f"📢 {ch}"
                elif ch.startswith('https://'):
                    link = ch
                    btn_text = "📢 Kanalga o'tish"
                else:
                    link = f"https://t.me/{ch}"
                    btn_text = f"📢 @{ch}"
                keyboard.append([InlineKeyboardButton(btn_text, url=link)])
            
            keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
            
            await update.message.reply_text(
                text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.HTML
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("📥 Instagramdan yuklash", callback_data="instagram_info")],
            [InlineKeyboardButton("🎨 Rasm yaratish (AI)", callback_data="generate_image")],
            [InlineKeyboardButton("❓ Yordam", callback_data="help")]
        ]
        
        await update.message.reply_text(
            f"{BOT_NAME}\n\n"
            "💎 <b>Xush kelibsiz!</b>\n\n"
            "Tanlang:\n"
            "• Instagram video yuklash\n"
            "• Matndan rasm yaratish",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )
    
    # ====================== RASM YARATISH ======================
    
    async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
        status = await update.message.reply_text("🎨 Rasm yaratilmoqda... Biroz kutib turing.")
        
        try:
            url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}"
            response = requests.get(url, timeout=60)
            
            if response.status_code == 200:
                photo_file = f"{DOWNLOAD_DIR}/generated_{uuid.uuid4()}.jpg"
                with open(photo_file, "wb") as f:
                    f.write(response.content)
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Yana yaratish", callback_data="new_image")],
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="start_menu")]
                ]
                
                with open(photo_file, 'rb') as photo:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=photo,
                        caption=f"✅ {BOT_NAME} orqali yaratildi!\n\n📝 Tavsif: {prompt}",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                
                await status.delete()
                os.remove(photo_file)
            else:
                await status.edit_text("❌ Rasm yaratib bo'lmadi. Iltimos, boshqa tavsif yozib ko'ring.")
                
        except Exception as e:
            logger.error(f"Rasm yaratishda xato: {e}")
            await status.edit_text("❌ Xatolik yuz berdi. Keyinroq urinib ko'ring.")
    
    # ====================== INSTAGRAM YUKLASH ======================
    
    async def download_instagram(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
        user_id = update.effective_user.id
        
        if not await check_subscription(update, context):
            await self.start(update, context)
            return
        
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
            'max_filesize': 50 * 1024 * 1024,
        }
        
        try:
            await status_msg.edit_text("⏳ Yuklab olinmoqda...")
            loop = asyncio.get_running_loop()
            
            def download_video():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
            
            await loop.run_in_executor(None, download_video)
            
            if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                await status_msg.edit_text("📤 Video yuborilmoqda...")
                
                await context.bot.send_video(
                    chat_id=update.effective_chat.id,
                    video=open(file_path, 'rb'),
                    caption=f"✅ {BOT_NAME}\n🔗 {url.split('?')[0]}",
                    supports_streaming=True
                )
                await status_msg.delete()
            else:
                await status_msg.edit_text("❌ Yuklab bo'lmadi.")
                
        except Exception as e:
            logger.error(f"Yuklashda xato: {e}")
            await status_msg.edit_text("❌ Xatolik yuz berdi. Link to'g'riligini tekshiring.")
        finally:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except:
                    pass
    
    # ====================== ADMIN PANEL ======================
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Siz admin emassiz!")
            return
        
        await update.message.reply_text(
            "🕹 <b>Admin boshqaruv paneli:</b>",
            reply_markup=admin_keyboard(),
            parse_mode=ParseMode.HTML
        )
    
    # ====================== CALLBACK HANDLER ======================
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        
        try:
            await query.answer()
        except Exception:
            pass
        
        # Obuna tekshirish
        if query.data == "check_sub":
            if await check_subscription(update, context):
                try:
                    await query.message.delete()
                except:
                    pass
                await self.start(update, context)
            else:
                try:
                    await query.answer("❌ Hali barcha kanallarga a'zo bo'lmadingiz!", show_alert=True)
                except:
                    pass
            return
        
        # Start menyu
        if query.data == "start_menu":
            try:
                await query.message.delete()
            except:
                pass
            await self.start(update, context)
            return
        
        # Instagram yuklash
        if query.data == "instagram_info":
            context.user_data['step'] = 'waiting_for_instagram'
            await query.message.edit_text("📥 Instagram video linkini yuboring:")
            return
        
        # Rasm yaratish
        if query.data == "generate_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await query.message.edit_text(
                "🖼 Rasm uchun tavsif yozing.\n\n"
                "Masalan: 'Toshkentdagi zamonaviy bino quyosh botishida'"
            )
            return
        
        if query.data == "new_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await query.message.edit_text("🖼 Yangi rasm uchun tavsif yozing:")
            return
        
        # Yordam
        if query.data == "help":
            help_text = (
                f"📚 {BOT_NAME} - Yordam\n\n"
                "🤖 Bot quyidagi imkoniyatlarga ega:\n\n"
                "1️⃣ Instagram'dan video yuklash\n"
                "2️⃣ Matndan AI rasm yaratish\n\n"
                "📝 Foydalanish:\n"
                "• Instagram videosini yuklash uchun link yuboring\n"
                "• Rasm yaratish uchun menyudan tanlang va tavsif yozing\n\n"
                "⚠️ Cheklovlar:\n"
                "• Video hajmi: 50 MB gacha\n"
                "• Video sifati: 720p gacha"
            )
            keyboard = [[InlineKeyboardButton("🏠 Bosh menyu", callback_data="start_menu")]]
            await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return
        
        # Admin paneli (faqat admin uchun)
        if user_id != ADMIN_ID:
            return
        
        try:
            if query.data == "admin_stat":
                conn = get_db_connection()
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                conn.close()
                await query.message.edit_text(
                    f"📊 Bot foydalanuvchilari: {count} ta",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            
            elif query.data == "toggle_sub":
                conn = get_db_connection()
                conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
                conn.commit()
                conn.close()
                await query.message.edit_text(
                    "🕹 Admin paneli:",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )
                await query.answer("✅ Majburiy obuna holati o'zgartirildi!")
            
            elif query.data == "add_channel":
                context.user_data['step'] = 'add_force_channel'
                await query.message.edit_text(
                    "➕ Yangi kanal username'ni yuboring (masalan: @KanalNomi yoki https://t.me/KanalNomi):",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")]])
                )
            
            elif query.data == "remove_channel":
                context.user_data['step'] = 'remove_force_channel'
                channels = get_force_channels()
                if channels:
                    txt = "➖ O'chirish kerak bo'lgan kanalni yuboring:\n\n" + "\n".join([f"• {ch}" for ch in channels])
                else:
                    txt = "➖ Hozircha kanallar ro'yxati bo'sh."
                await query.message.edit_text(
                    txt,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel")]])
                )
            
            elif query.data == "list_channels":
                channels = get_force_channels()
                if channels:
                    txt = "📋 <b>Majburiy kanallar ro'yxati:</b>\n\n" + "\n".join([f"• {ch}" for ch in channels])
                else:
                    txt = "📋 Hozircha majburiy kanal yo'q."
                await query.message.edit_text(txt, reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            
            elif query.data == "send_help":
                await query.message.edit_text(
                    "📢 Reklama yuborish uchun:\n"
                    "1. Xabarni botga yuboring\n"
                    "2. O'sha xabarga reply qilib /send buyrug'ini yuboring\n\n"
                    "Bot bu xabarni barcha foydalanuvchilarga forward qiladi.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Orqaga", callback_data="admin_panel")]])
                )
            
            elif query.data == "admin_panel":
                await query.message.edit_text(
                    "🕹 <b>Admin boshqaruv paneli:</b>",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            
            elif query.data == "cancel":
                context.user_data['step'] = None
                await query.message.edit_text(
                    "🕹 Admin paneli:",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )
                
        except Exception as e:
            logger.error(f"Callback xatosi: {e}")
            try:
                await query.message.edit_text(
                    "Xatolik yuz berdi. Qaytadan urinib ko'ring.",
                    reply_markup=admin_keyboard()
                )
            except:
                pass
    
    # ====================== REKLAMA YUBORISH ======================
    
    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            return
        
        if not update.message.reply_to_message:
            await update.message.reply_text("❌ Xabarga reply qilib /send yozing.")
            return
        
        status = await update.message.reply_text("📢 Reklama yuborilmoqda...")
        
        conn = get_db_connection()
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        
        sent = 0
        failed = 0
        
        for u in users:
            try:
                await context.bot.copy_message(
                    chat_id=u['user_id'],
                    from_chat_id=update.effective_chat.id,
                    message_id=update.message.reply_to_message.message_id
                )
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                failed += 1
        
        await status.edit_text(f"✅ Tugatildi!\n\n📊 Yetkazildi: {sent}\n❌ Bloklaganlar: {failed}")
    
    # ====================== MATN XABARLARNI QAYTA ISHLASH ======================
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')
        
        # Admin - kanal qo'shish
        if user_id == ADMIN_ID and step == 'add_force_channel':
            if add_force_channel(text):
                await update.message.reply_text(
                    f"✅ Kanal qo'shildi: {text}",
                    reply_markup=admin_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            else:
                await update.message.reply_text(
                    "⚠️ Bu kanal allaqachon ro'yxatda bor.",
                    reply_markup=admin_keyboard()
                )
            context.user_data['step'] = None
            return
        
        # Admin - kanal o'chirish
        if user_id == ADMIN_ID and step == 'remove_force_channel':
            remove_force_channel(text)
            await update.message.reply_text(
                f"✅ Kanal o'chirildi: {text}",
                reply_markup=admin_keyboard(),
                parse_mode=ParseMode.HTML
            )
            context.user_data['step'] = None
            return
        
        # Rasm yaratish uchun prompt
        if step == 'waiting_for_prompt':
            context.user_data['step'] = None
            await self.generate_image(update, context, text)
            return
        
        # Instagram linkini kutish
        if step == 'waiting_for_instagram':
            context.user_data['step'] = None
            if "instagram.com" in text.lower():
                await self.download_instagram(update, context, text)
            else:
                await update.message.reply_text("❌ Iltimos, to'g'ri Instagram linkini yuboring!")
            return
        
        # Instagram linki
        if "instagram.com" in text.lower():
            await self.download_instagram(update, context, text)
            return
        
        # Boshqa xabarlar
        await update.message.reply_text(
            "❓ Iltimos, menyudan biror amalni tanlang yoki Instagram linkini yuboring."
        )

# ====================== ASOSIY DASTUR ======================

if __name__ == "__main__":
    bot = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("admin", bot.admin_panel))
    app.add_handler(CommandHandler("send", bot.broadcast_send))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    
    print(f"🚀 {BOT_NAME} muvaffaqiyatli ishga tushdi!")
    print("✅ Barcha xususiyatlar: Instagram yuklash + AI rasm + Ko'p kanal obuna")
    app.run_polling(drop_pending_updates=True)
