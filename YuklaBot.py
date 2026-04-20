import os
import asyncio
import logging
import uuid
import imageio_ffmpeg
import requests
import time
import hashlib
import random
import math
import textwrap  # ← Yangi qo'shildi: tez va to'g'ri matn bo'lish uchun
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode
import asyncpg
import yt_dlp

# ====================== LOGGING & ENV ======================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@zorekan_bot")
ADMIN_ID = int(os.getenv("ADMIN_ID", "6698039974"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
DATABASE_URL = os.getenv("DATABASE_URL")
DOWNLOAD_DIR = "downloads"
BOT_NAME = "🌟 NeoGlow bot"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ====================== HANDWRITING FONTLARI ======================
HANDWRITING_FONTS = {
    "universal": {"name": "Universal Yozuv", "font_file": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "size": 28, "line_spacing": 45, "margin_left": 80, "margin_right": 60, "margin_top": 100, "margin_bottom": 80, "color": (0, 0, 100), "variation": True},
    "dangasa": {"name": "Dangasa Yozuv", "font_file": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "size": 32, "line_spacing": 50, "margin_left": 70, "margin_right": 50, "margin_top": 90, "margin_bottom": 70, "color": (50, 50, 50), "variation": True},
    "egri": {"name": "Egri Yozuv", "font_file": "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf", "size": 30, "line_spacing": 48, "margin_left": 75, "margin_right": 55, "margin_top": 95, "margin_bottom": 75, "color": (0, 0, 0), "variation": True},
    "chiroyli": {"name": "Chiroyli Yozuv", "font_file": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf", "size": 28, "line_spacing": 44, "margin_left": 85, "margin_right": 65, "margin_top": 105, "margin_bottom": 85, "color": (0, 51, 102), "variation": False},
}

# ====================== OPENAI & AI ======================
openai_client = None
AI_ACTIVE = "pollinations"
try:
    import openai
    from openai import OpenAI
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        AI_ACTIVE = "openai"
        logger.info("OpenAI modern client muvaffaqiyatli ishga tushdi")
except Exception as e:
    logger.warning(f"OpenAI client xatosi: {e}. Pollinations ishlatiladi.")

try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

# ====================== DATABASE ======================
db_pool = None
async def init_db_pool():
    global db_pool
    if not DATABASE_URL:
        logger.error("DATABASE_URL topilmadi!")
        raise RuntimeError("DATABASE_URL kerak")
    db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10, command_timeout=60, timeout=60)
    logger.info("✅ PostgreSQL pool muvaffaqiyatli yaratildi")

async def close_db_pool():
    global db_pool
    if db_pool:
        await db_pool.close()

async def get_db():
    return await db_pool.acquire()

async def release_db(conn):
    await db_pool.release(conn)

# ====================== DATABASE INIT ======================
async def init_db():
    conn = await get_db()
    try:
        await conn.execute("""CREATE TABLE IF NOT EXISTS users (...)""")  # oldingi kod bilan bir xil
        # ... (barcha CREATE TABLE lar o'zgarmadi, joy tejash uchun qisqartirildi)
        logger.info("✅ Baza tayyor")
    finally:
        await release_db(conn)

# ====================== YORDAMCHI FUNKSIYALAR ======================
# ... (oldindagi barcha yordamchi funksiyalar: extract_channel_id, add_force_channel va h.k. o'zgarmadi)

# ====================== PREMIUM & LIMIT ======================
# ... (is_user_premium, add_premium_days, check_and_increment_usage, get_usage_info o'zgarmadi)

# ====================== AI CACHE & GENERATION ======================
# ... (ai_text, ai_image, resize_image_smart, add_watermark o'zgarmadi)

# ====================== OPTIMALLASHTIRILGAN HANDWRITING ======================
def create_handwriting_image(text: str, style_key: str = "universal", page_num: int = 1) -> bytes:
    style = HANDWRITING_FONTS.get(style_key, HANDWRITING_FONTS["universal"])
    width, height = 1240, 1754
    img = Image.new('RGB', (width, height), color='white')
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(style["font_file"], style["size"])
    except:
        font = ImageFont.load_default()

    max_width = width - style["margin_left"] - style["margin_right"]
    words = text.split()
    lines = []
    current_line = []
    current_width = 0

    for word in words:
        bbox = draw.textbbox((0, 0), word, font=font)
        word_width = bbox[2] - bbox[0]
        if current_width + word_width <= max_width:
            current_line.append(word)
            current_width += word_width + 15
        else:
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
            current_width = word_width
    if current_line:
        lines.append(' '.join(current_line))

    y = style["margin_top"]
    line_height = style["line_spacing"]

    if style_key == "universal":
        for i in range(0, height, 40):
            draw.line([(40, i), (width-40, i)], fill=(200, 220, 255), width=1)
        draw.line([(style["margin_left"]-20, 0), (style["margin_left"]-20, height)], fill=(255, 200, 200), width=2)

    for line in lines:
        if y > height - style["margin_bottom"]:
            break
        x = style["margin_left"]

        if style.get("variation", False):
            char_x = x
            for char in line:
                offset_y = random.randint(-3, 3)
                offset_x = random.randint(-2, 2)
                rotation = random.randint(-4, 4)

                char_img = Image.new('RGBA', (60, 60), (255, 255, 255, 0))
                char_draw = ImageDraw.Draw(char_img)
                char_draw.text((30, 30), char, font=font, fill=style["color"], anchor="mm")
                char_img = char_img.rotate(rotation, expand=True, resample=Image.BICUBIC)
                img.paste(char_img, (int(char_x + offset_x), int(y + offset_y)), char_img)

                bbox = draw.textbbox((0, 0), char, font=font)
                char_x += (bbox[2] - bbox[0]) * 0.92
        else:
            draw.text((x, y), line, font=font, fill=style["color"])

        y += line_height

    if page_num > 1:
        small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
        draw.text((width-100, height-50), f"- {page_num} -", font=small_font, fill=(150, 150, 150))

    output = BytesIO()
    img.save(output, format='PNG', quality=95)
    return output.getvalue()

def create_handwriting_pages(text: str, style_key: str = "universal") -> list:
    """Tez va to'g'ri varaqlarga bo'lish (optimallashtirilgan)"""
    style = HANDWRITING_FONTS.get(style_key, HANDWRITING_FONTS["universal"])
    width, height = 1240, 1754
    max_px_width = width - style["margin_left"] - style["margin_right"]

    try:
        font = ImageFont.truetype(style["font_file"], style["size"])
    except:
        font = ImageFont.load_default()

    # O'zbekcha uchun aniqroq hisob
    test_text = "abcdefghijklmno'pqrstuvwx yz sh ch g' o' ü ö"
    draw_test = ImageDraw.Draw(Image.new('RGB', (1,1)))
    bbox = draw_test.textbbox((0, 0), test_text, font=font)
    avg_char_width = (bbox[2] - bbox[0]) / len(test_text) * 1.12

    chars_per_line = int(max_px_width / avg_char_width)
    usable_height = height - style["margin_top"] - style["margin_bottom"]
    lines_per_page = max(1, int(usable_height / style["line_spacing"]))

    # Eng tez va to'g'ri usul
    wrapped_lines = textwrap.wrap(text, width=chars_per_line, break_long_words=False)

    pages = []
    page_num = 1
    for i in range(0, len(wrapped_lines), lines_per_page):
        page_lines = wrapped_lines[i:i + lines_per_page]
        page_text = '\n'.join(page_lines)
        img_bytes = create_handwriting_image(page_text, style_key, page_num)
        pages.append(img_bytes)
        page_num += 1

    return pages

# ====================== BOT KLASSI (qolgan qismi o'zgarmadi) ======================
class InstagramDownloader:
    # ... (barcha eski metodlar: start, ai_studio, generate_image, download_instagram, admin_panel va h.k. o'zgarmadi)

    async def handle_handwriting(self, update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
        user_id = update.effective_user.id
        style = context.user_data.get('handwriting_style', 'universal')

        status = await update.message.reply_text("✍️ Matn qo'lda yozilmoqda...")

        try:
            if len(text) < 5:
                await status.edit_text("❌ Matn juda qisqa. Kamida 5 ta belgi kiriting.")
                return
            if len(text) > 15000:  # himoya
                await status.edit_text("❌ Matn juda uzun. Maksimal 15000 ta belgi.")
                return

            # LIMIT YO'Q! To'g'ridan-to'g'ri ishlaydi
            pages = create_handwriting_pages(text, style)

            await status.edit_text(f"📄 {len(pages)} ta varaq yaratildi. Yuborilmoqda...")

            media_group = [InputMediaPhoto(media=page_bytes) for page_bytes in pages[:10]]

            if len(media_group) == 1:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id,
                    photo=pages[0],
                    caption=f"✍️ {HANDWRITING_FONTS[style]['name']} uslubi\n{BOT_USERNAME}"
                )
            else:
                await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group)
                await update.message.reply_text(
                    f"✅ {len(pages)} ta varaq tayyor!\n🎨 Uslub: {HANDWRITING_FONTS[style]['name']}\n{BOT_USERNAME}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]])
                )

            await status.delete()

        except Exception as e:
            logger.error(f"Handwriting error: {e}")
            await status.edit_text("❌ Xatolik yuz berdi.")
        finally:
            context.user_data['step'] = None
            context.user_data.pop('handwriting_style', None)

    # Qolgan barcha metodlar (handle_text, callback_handler va h.k.) o'zgarmadi

# ====================== MAIN ======================
async def setup_bot():
    await init_db_pool()
    await init_db()
    bot_instance = InstagramDownloader()
    application = ApplicationBuilder().token(TOKEN).build()
    application.add_handler(CommandHandler("start", bot_instance.start))
    application.add_handler(CommandHandler("admin", bot_instance.admin_panel))
    application.add_handler(CommandHandler("send", bot_instance.broadcast_send))
    application.add_handler(CallbackQueryHandler(bot_instance.callback_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot_instance.handle_text))
    return application

def main():
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        application = loop.run_until_complete(setup_bot())
        logger.info(f"🚀 {BOT_NAME} tez va yengil rejimda ishga tushdi!")
        application.run_polling(drop_pending_updates=True)
    except Exception as e:
        logger.error(f"Xatolik: {e}")
    finally:
        try:
            loop.run_until_complete(close_db_pool())
        except:
            pass

if __name__ == "__main__":
    main()
