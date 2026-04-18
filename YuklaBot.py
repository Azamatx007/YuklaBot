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

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 6698039974
DOWNLOAD_DIR = "downloads"
BOT_NAME = "🌟 Zo'r Ekan Bot"

# Papka yaratish
if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ====================== KLASSNI TO'LIQ YOZISH ======================

class YuklaBot:
    def __init__(self):
        self.db_path = "bot_data.db"
        self.init_database()
    
    def init_database(self):
        """Ma'lumotlar bazasini yaratish"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_date TEXT,
                download_count INTEGER DEFAULT 0
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS channels (
                channel_id INTEGER PRIMARY KEY,
                channel_username TEXT,
                channel_name TEXT
            )
        ''')
        conn.commit()
        conn.close()
    
    # ====================== MAJBURIY OBUNA TEKSHIRISH ======================
    
    async def check_subscription(self, user_id, context: ContextTypes.DEFAULT_TYPE):
        """Foydalanuvchi barcha kanallarga obuna bo'lganini tekshirish"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT channel_username FROM channels")
        channels = cursor.fetchall()
        conn.close()
        
        not_subscribed = []
        
        for (channel_username,) in channels:
            try:
                member = await context.bot.get_chat_member(f"@{channel_username}", user_id)
                if member.status in ['left', 'kicked']:
                    not_subscribed.append(channel_username)
            except:
                continue
                
        return not_subscribed
    
    # ====================== START ======================
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        user = update.effective_user
        
        # Foydalanuvchini bazaga qo'shish
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO users (user_id, username, first_name, joined_date)
            VALUES (?, ?, ?, datetime('now'))
        ''', (user_id, user.username, user.first_name))
        conn.commit()
        conn.close()
        
        # Obuna tekshirish
        not_subscribed = await self.check_subscription(user_id, context)
        
        if not_subscribed:
            keyboard = []
            for channel in not_subscribed:
                keyboard.append([InlineKeyboardButton(f"📢 {channel}", url=f"https://t.me/{channel}")])
            keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
            
            channels_text = "\n".join([f"👉 @{ch}" for ch in not_subscribed])
            await update.message.reply_text(
                f"❗ Botdan foydalanish uchun quyidagi kanallarga obuna bo'ling:\n\n{channels_text}\n\n"
                "Obuna bo'lgach, \"✅ Tekshirish\" tugmasini bosing.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        keyboard = [
            [InlineKeyboardButton("📥 Instagramdan yuklash", callback_data="instagram_download")],
            [InlineKeyboardButton("🎨 Rasm yaratish (AI)", callback_data="generate_image")],
            [InlineKeyboardButton("❓ Yordam", callback_data="help")]
        ]
        
        await update.message.reply_text(
            f"{BOT_NAME}\n\n"
            "🇺🇿 Salom! Men sizga yordam berishga tayyorman.\n\n"
            "Tanlang:\n"
            "• Instagram videolarini yuklash\n"
            "• Matndan rasm yaratish",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    # ====================== RASM YARATISH ======================
    
    async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
        status = await update.message.reply_text("🎨 Rasm yaratilmoqda... Biroz kutib turing.")
        
        try:
            # Bepul AI rasm generator
            url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}"
            response = requests.get(url, timeout=60)
            
            if response.status_code == 200:
                photo_file = f"{DOWNLOAD_DIR}/generated_{uuid.uuid4()}.jpg"
                with open(photo_file, "wb") as f:
                    f.write(response.content)
                
                keyboard = [
                    [InlineKeyboardButton("🔄 Yana yaratish", callback_data="new_image")],
                    [InlineKeyboardButton("🏠 Bosh menyu", callback_data="start")]
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
        
        # Obuna tekshirish
        not_subscribed = await self.check_subscription(user_id, context)
        if not_subscribed:
            await self.start(update, context)
            return
        
        status_msg = await update.message.reply_text("⏳ Video yuklanmoqda...")
        
        try:
            # yt-dlp sozlamalari
            ydl_opts = {
                'outtmpl': f'{DOWNLOAD_DIR}/%(title)s_%(id)s.%(ext)s',
                'quiet': True,
                'no_warnings': True,
                'extract_flat': False,
                'format': 'best[height<=720]',
                'max_filesize': 50 * 1024 * 1024,  # 50 MB
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)
                
                # Fayl kengaytmasini topish
                if not os.path.exists(filename):
                    for ext in ['.mp4', '.webm', '.mkv']:
                        test_file = filename.rsplit('.', 1)[0] + ext
                        if os.path.exists(test_file):
                            filename = test_file
                            break
                
                # Fayl hajmini tekshirish
                file_size = os.path.getsize(filename) / (1024 * 1024)
                
                if file_size > 50:
                    await status_msg.edit_text("❌ Video hajmi 50 MB dan katta!")
                    os.remove(filename)
                    return
                
                await status_msg.edit_text("📤 Video yuborilmoqda...")
                
                # Videoni yuborish
                with open(filename, 'rb') as video:
                    await context.bot.send_video(
                        chat_id=update.effective_chat.id,
                        video=video,
                        caption=f"✅ {BOT_NAME}\n📹 {info.get('title', 'Video')}",
                        supports_streaming=True
                    )
                
                await status_msg.delete()
                os.remove(filename)
                
                # Yuklashlar sonini yangilash
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET download_count = download_count + 1 WHERE user_id = ?", (user_id,))
                conn.commit()
                conn.close()
                
        except Exception as e:
            logger.error(f"Yuklashda xato: {e}")
            await status_msg.edit_text("❌ Video yuklab bo'lmadi. Link to'g'riligini tekshiring.")
    
    # ====================== CALLBACK HANDLER ======================
    
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = update.effective_user.id
        
        if query.data == "check_sub":
            not_subscribed = await self.check_subscription(user_id, context)
            if not not_subscribed:
                await query.message.delete()
                await self.start(update, context)
            else:
                await query.answer("❌ Hali ham obuna bo'lmagansiz!", show_alert=True)
                
        elif query.data == "instagram_download":
            context.user_data['step'] = 'waiting_for_instagram'
            await query.message.edit_text("📥 Instagram video linkini yuboring:")
            
        elif query.data == "generate_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await query.message.edit_text(
                "🖼 Rasm uchun tavsif yozing.\n\n"
                "Masalan: 'Toshkentdagi zamonaviy bino quyosh botishida'"
            )
            
        elif query.data == "new_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await query.message.edit_text("🖼 Yangi rasm uchun tavsif yozing:")
            
        elif query.data == "start":
            await query.message.delete()
            await self.start(update, context)
            
        elif query.data == "help":
            help_text = (
                f"📚 {BOT_NAME} - Yordam\n\n"
                "🤖 Bot quyidagi imkoniyatlarga ega:\n\n"
                "1️⃣ Instagram'dan video yuklash\n"
                "2️⃣ Matndan AI rasm yaratish\n\n"
                "📝 Foydalanish:\n"
                "• Instagram videosini yuklash uchun menyudan tanlang va link yuboring\n"
                "• Rasm yaratish uchun menyudan tanlang va ingliz tilida tavsif yozing\n\n"
                "⚠️ Cheklovlar:\n"
                "• Video hajmi: 50 MB gacha\n"
                "• Video sifati: 720p gacha\n\n"
                "👨‍💻 Admin: @YourUsername"
            )
            keyboard = [[InlineKeyboardButton("🏠 Bosh menyu", callback_data="start")]]
            await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    # ====================== MATN XABARLARNI QAYTA ISHLASH ======================
    
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text.strip()
        step = context.user_data.get('step')
        
        if step == 'waiting_for_prompt':
            context.user_data['step'] = None
            await self.generate_image(update, context, text)
            
        elif step == 'waiting_for_instagram':
            context.user_data['step'] = None
            if "instagram.com" in text.lower():
                await self.download_instagram(update, context, text)
            else:
                await update.message.reply_text("❌ Iltimos, to'g'ri Instagram linkini yuboring!")
                
        elif "instagram.com" in text.lower():
            await self.download_instagram(update, context, text)
            
        else:
            await update.message.reply_text(
                "❓ Tushunmadim. Iltimos, menyudan biror amalni tanlang yoki Instagram linkini yuboring."
            )
    
    # ====================== ADMIN PANEL ======================
    
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        
        if user_id != ADMIN_ID:
            await update.message.reply_text("⛔ Siz admin emassiz!")
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        user_count = cursor.fetchone()[0]
        conn.close()
        
        keyboard = [
            [InlineKeyboardButton("📊 Statistika", callback_data="admin_stats")],
            [InlineKeyboardButton("📢 Kanallar", callback_data="admin_channels")],
            [InlineKeyboardButton("📨 Xabar yuborish", callback_data="admin_broadcast")]
        ]
        
        await update.message.reply_text(
            f"👨‍💻 Admin Panel\n\n"
            f"👥 Foydalanuvchilar: {user_count} ta\n\n"
            "Amalni tanlang:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

# ====================== ASOSIY DASTUR ======================

if __name__ == "__main__":
    bot = YuklaBot()
    app = ApplicationBuilder().token(TOKEN).build()
    
    # Handlerlarni qo'shish
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("admin", bot.admin_panel))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    
    print(f"🚀 {BOT_NAME} ishga tushdi!")
    print("✅ Barcha modullar yuklandi!")
    app.run_polling(drop_pending_updates=True)
