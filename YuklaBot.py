import os
import asyncio
import logging
import sqlite3
import yt_dlp
import uuid
import imageio_ffmpeg
import requests  # Yangi qo'shildi
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

# ... DATABASE qismi (oldingi kod bilan bir xil qoldiring) ...

# ====================== MAJBURIY OBUNA (oldingi kod bilan bir xil) ======================
# check_subscription funksiyasini avvalgi versiyangizdan qoldiring

# ====================== YANGI: RASM YARATISH ======================
async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
    status = await update.message.reply_text("🎨 Rasm yaratilmoqda... Biroz kutib turing.")

    try:
        # Bepul oddiy variant (Pollinations.ai)
        url = f"https://image.pollinations.ai/prompt/{prompt.replace(' ', '%20')}"
        response = requests.get(url, timeout=30)
        
        if response.status_code == 200:
            photo_file = f"generated_{uuid.uuid4()}.jpg"
            with open(photo_file, "wb") as f:
                f.write(response.content)

            keyboard = [
                [InlineKeyboardButton("🔄 Yana yaratish", callback_data="new_image")],
                [InlineKeyboardButton("📥 Instagram yuklash", callback_data="start_download")]
            ]

            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=open(photo_file, 'rb'),
                caption=f"✅ {BOT_NAME} orqali yaratildi!\n\nPrompt: {prompt}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            await status.delete()
            os.remove(photo_file)
        else:
            await status.edit_text("❌ Rasm yaratib bo‘lmadi. Boshqa prompt sinab ko‘ring.")
    except Exception as e:
        logger.error(f"Image generation error: {e}")
        await status.edit_text("❌ Xatolik yuz berdi. Keyinroq urinib ko‘ring.")

# ====================== START VA MENYU ======================
async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Majburiy obuna tekshirish (oldingi kod)
    if not await check_subscription(update, context):
        # ... obuna so'rovchi kod ...
        return

    keyboard = [
        [InlineKeyboardButton("📥 Instagramdan yuklash", callback_data="instagram_download")],
        [InlineKeyboardButton("🎨 Rasm yaratish (AI)", callback_data="generate_image")],
        [InlineKeyboardButton("✍️ Uzun komment yozish", callback_data="long_comment")],
        [InlineKeyboardButton("❓ Yordam", callback_data="help")]
    ]

    await update.message.reply_text(
        f"🌟 <b>{BOT_NAME}</b>\n\n"
        "Tanlang:\n"
        "• Instagram videolarini yuklash\n"
        "• Matndan rasm yaratish\n"
        "• Qisqa matndan uzun kommentariya yasash",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.HTML
    )

# Callback handler ni yangilang (qisqa versiya)
async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "instagram_download":
        await query.message.edit_text("📥 Instagram linkini yuboring:")
        return

    if query.data == "generate_image":
        context.user_data['step'] = 'waiting_for_prompt'
        await query.message.edit_text("🖼 Rasm uchun tavsif yozing (masalan: 'Samarkanddagi qadimiy masjid quyosh botishida'):")
        return

    if query.data == "new_image":
        context.user_data['step'] = 'waiting_for_prompt'
        await query.message.edit_text("Yangi rasm uchun tavsif yozing:")
        return

    # Boshqa tugmalar (help, long_comment va h.k.) ni keyinroq qo'shamiz

# Handle text ni yangilang
async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()
    step = context.user_data.get('step')

    if step == 'waiting_for_prompt':
        context.user_data['step'] = None
        await self.generate_image(update, context, text)
        return

    # Instagram yuklash qismi (oldingi kod)
    if "instagram.com" in text.lower():
        if not await check_subscription(update, context):
            await self.start(update, context)
            return
        # ... Instagram download kodingiz ...
        return

    await update.message.reply_text("Iltimos, menyudan tanlang yoki Instagram linkini yuboring.")

# ====================== RUN ======================
if __name__ == "__main__":
    bot_logic = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", bot_logic.start))
    app.add_handler(CommandHandler("admin", bot_logic.admin_panel))
    app.add_handler(CallbackQueryHandler(bot_logic.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_logic.handle_text))

    print(f"🚀 {BOT_NAME} ishga tushdi! (Instagram + AI Rasm)")
    app.run_polling(drop_pending_updates=True)
