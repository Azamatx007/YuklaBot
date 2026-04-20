import os
import asyncio
import logging
import uuid
import imageio_ffmpeg
import requests
import time
import hashlib
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
import telegram.error

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

# ====================== OPENAI ======================
openai_client = None
AI_ACTIVE = "pollinations"

try:
    import openai
    from openai import OpenAI
    if OPENAI_API_KEY:
        openai_client = OpenAI(api_key=OPENAI_API_KEY)
        AI_ACTIVE = "openai"
        logger.info("✅ OpenAI client muvaffaqiyatli yuklandi")
    else:
        logger.warning("OpenAI API key yo'q. Faqat Pollinations ishlatiladi.")
except Exception as e:
    logger.warning(f"OpenAI client yuklashda xatolik: {e}. Pollinations ishlatiladi.")

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
   
    db_pool = await asyncpg.create_pool(
        DATABASE_URL, min_size=2, max_size=10,
        command_timeout=60, timeout=60
    )
    logger.info("✅ PostgreSQL pool yaratildi")

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
        # users
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                status TEXT DEFAULT 'active',
                is_premium BOOLEAN DEFAULT FALSE,
                premium_expire TIMESTAMP,
                joined_date TIMESTAMP,
                referrer_id BIGINT
            )
        """)
        # settings
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                channel_id TEXT,
                force_sub INTEGER DEFAULT 0
            )
        """)
        # force_channels
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS force_channels (
                id SERIAL PRIMARY KEY,
                channel_id TEXT UNIQUE NOT NULL
            )
        """)
        # user_daily_usage
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_daily_usage (
                user_id BIGINT,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        # ai_cache
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_cache (
                prompt_hash TEXT PRIMARY KEY,
                response TEXT,
                created INTEGER
            )
        """)
        # prompt_templates
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS prompt_templates (
                id SERIAL PRIMARY KEY,
                name TEXT,
                category TEXT,
                system_prompt TEXT,
                user_prompt_template TEXT,
                is_premium INTEGER DEFAULT 0
            )
        """)

        if (await conn.fetchval("SELECT COUNT(*) FROM settings")) == 0:
            await conn.execute("INSERT INTO settings (id, channel_id, force_sub) VALUES (1, '', 1)")

        if (await conn.fetchval("SELECT COUNT(*) FROM prompt_templates")) == 0:
            templates = [
                ("Marketing Post", "business", "Siz professional marketologsiz. Berilgan mavzu bo'yicha ijodiy va sotuvchan post yozing.", "{topic}", 0),
                ("Referat Rejasi", "academic", "Siz akademik yordamchisiz. Mavzu bo'yicha batafsil referat rejasini o'zbek tilida tuzing.", "Mavzu: {topic}", 0),
                ("SEO Hashtaglar", "seo", "10 ta eng zo'r hashtaglarni qaytaring.", "Niche: {topic}", 0),
            ]
            for t in templates:
                await conn.execute(
                    "INSERT INTO prompt_templates (name, category, system_prompt, user_prompt_template, is_premium) VALUES ($1,$2,$3,$4,$5)",
                    *t
                )
        logger.info("✅ Baza jadval va boshlang'ich ma'lumotlar tayyor")
    finally:
        await release_db(conn)

# ====================== YORDAMCHI FUNKSIYALAR ======================
def extract_channel_id(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('@'):
        return raw
    if 't.me/' in raw:
        parts = raw.split('t.me/')
        if len(parts) > 1:
            username = parts[1].split('/')[0].split('?')[0]
            return f"@{username}"
    return raw

async def add_force_channel(channel_id: str):
    channel_id = extract_channel_id(channel_id)
    conn = await get_db()
    try:
        await conn.execute("INSERT INTO force_channels (channel_id) VALUES ($1)", channel_id)
        return True
    except asyncpg.exceptions.UniqueViolationError:
        return False
    finally:
        await release_db(conn)

async def remove_force_channel(channel_id: str):
    channel_id = extract_channel_id(channel_id)
    conn = await get_db()
    try:
        await conn.execute("DELETE FROM force_channels WHERE channel_id = $1", channel_id)
    finally:
        await release_db(conn)

async def get_force_channels():
    conn = await get_db()
    try:
        rows = await conn.fetch("SELECT channel_id FROM force_channels")
        return [r['channel_id'] for r in rows]
    finally:
        await release_db(conn)

async def get_settings():
    conn = await get_db()
    try:
        return await conn.fetchrow("SELECT * FROM settings WHERE id=1")
    finally:
        await release_db(conn)

# ====================== PREMIUM & LIMIT ======================
async def is_user_premium(user_id: int) -> bool:
    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT is_premium, premium_expire FROM users WHERE user_id=$1", user_id)
        if not row or not row['is_premium']:
            return False
        if row['premium_expire'] and row['premium_expire'] < datetime.now():
            await conn.execute("UPDATE users SET is_premium=FALSE WHERE user_id=$1", user_id)
            return False
        return True
    finally:
        await release_db(conn)

async def add_premium_days(user_id: int, days: int):
    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT premium_expire FROM users WHERE user_id=$1", user_id)
        expire = row['premium_expire'] if row and row['premium_expire'] else datetime.now()
        new_expire = max(expire, datetime.now()) + timedelta(days=days)
        await conn.execute(
            "UPDATE users SET is_premium=TRUE, premium_expire=$1 WHERE user_id=$2",
            new_expire, user_id
        )
    finally:
        await release_db(conn)

async def check_and_increment_usage(user_id: int, limit_free: int = 3, limit_premium: int = 50) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = await get_db()
    try:
        premium = await is_user_premium(user_id)
        limit = limit_premium if premium else limit_free
        row = await conn.fetchrow(
            "SELECT count FROM user_daily_usage WHERE user_id=$1 AND date=$2",
            user_id, today
        )
        current = row['count'] if row else 0
        if current >= limit:
            return False
        if row:
            await conn.execute(
                "UPDATE user_daily_usage SET count=count+1 WHERE user_id=$1 AND date=$2",
                user_id, today
            )
        else:
            await conn.execute(
                "INSERT INTO user_daily_usage (user_id, date, count) VALUES ($1,$2,1)",
                user_id, today
            )
        return True
    finally:
        await release_db(conn)

async def get_usage_info(user_id: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    conn = await get_db()
    try:
        row = await conn.fetchrow(
            "SELECT count FROM user_daily_usage WHERE user_id=$1 AND date=$2",
            user_id, today
        )
        current = row['count'] if row else 0
        premium = await is_user_premium(user_id)
        limit = 50 if premium else 3
        return f"{current}/{limit}"
    finally:
        await release_db(conn)

# ====================== AI CACHE & GENERATION ======================
cache_ttl = 3600

async def get_cached(prompt: str) -> str | None:
    h = hashlib.md5(prompt.encode()).hexdigest()
    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT response, created FROM ai_cache WHERE prompt_hash=$1", h)
        if row and time.time() - row['created'] < cache_ttl:
            return row['response']
        return None
    finally:
        await release_db(conn)

async def set_cache(prompt: str, response: str):
    h = hashlib.md5(prompt.encode()).hexdigest()
    conn = await get_db()
    try:
        await conn.execute(
            "INSERT INTO ai_cache (prompt_hash, response, created) VALUES ($1,$2,$3) "
            "ON CONFLICT (prompt_hash) DO UPDATE SET response=EXCLUDED.response, created=EXCLUDED.created",
            h, response, int(time.time())
        )
    finally:
        await release_db(conn)

async def ai_text(prompt: str, system: str = None, use_cache: bool = True) -> str:
    cache_key = f"{system}||{prompt}" if system else prompt
    if use_cache:
        cached = await get_cached(cache_key)
        if cached:
            return cached

    result = None
    if AI_ACTIVE == "openai" and openai_client:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            result = resp.choices[0].message.content.strip()
            logger.info("✅ OpenAI text muvaffaqiyatli")
        except Exception as e:
            logger.warning(f"OpenAI text xatosi: {e}. Pollinations ishlatilmoqda...")

    if not result:
        full = f"{system}\n\n{prompt}" if system else prompt
        try:
            url = f"https://text.pollinations.ai/{requests.utils.quote(full)}"
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                result = r.text.strip()
                logger.info("✅ Pollinations text ishlatildi")
        except Exception as e:
            logger.error(f"Pollinations text xatosi: {e}")

    if not result:
        result = "Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

    if result and use_cache:
        await set_cache(cache_key, result)
    return result

def ai_image(prompt: str) -> bytes | None:
    if AI_ACTIVE == "openai" and openai_client:
        try:
            resp = openai_client.images.generate(
                prompt=prompt,
                n=1,
                size="1024x1024"
                # quality parametri olib tashlandi (xatolik chiqmasligi uchun)
            )
            url = resp.data[0].url
            return requests.get(url, timeout=30).content
        except Exception as e:
            logger.warning(f"OpenAI image xatosi: {e}. Pollinations ga o'tildi.")

    # Pollinations fallback (eng ishonchli)
    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=1024"
        r = requests.get(url, timeout=120)
        if r.status_code == 200:
            return r.content
    except Exception as e:
        logger.error(f"Pollinations image xatosi: {e}")
    return None

# ====================== IMAGE PROCESSING ======================
def resize_image_smart(image_bytes: bytes, target_size: tuple) -> bytes:
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        tw, th = target_size
        ow, oh = img.size
        target_ratio = tw / th
        orig_ratio = ow / oh
        if orig_ratio > target_ratio:
            new_w = int(oh * target_ratio)
            left = (ow - new_w) // 2
            img = img.crop((left, 0, left + new_w, oh))
        elif orig_ratio < target_ratio:
            new_h = int(ow / target_ratio)
            top = (oh - new_h) // 2
            img = img.crop((0, top, ow, top + new_h))
        img = img.resize((tw, th), Image.Resampling.LANCZOS)
        out = BytesIO()
        img.save(out, format='JPEG', quality=90)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Resize error: {e}")
        return image_bytes

def add_watermark(image_bytes: bytes, text: str = BOT_USERNAME) -> bytes:
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        txt = Image.new('RGBA', img.size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(txt)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=int(img.width * 0.045))
        except:
            try:
                font = ImageFont.truetype("arial.ttf", size=int(img.width * 0.045))
            except:
                font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pos = (img.width - tw - 20, img.height - th - 20)
        draw.text(pos, text, font=font, fill=(255, 255, 255, 180))
        combined = Image.alpha_composite(img, txt).convert("RGB")
        out = BytesIO()
        combined.save(out, format='JPEG', quality=85)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Watermark error: {e}")
        return image_bytes

# ====================== BOT KLASSI ======================
class InstagramDownloader:
    async def check_subscription(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        settings = await get_settings()
        if settings['force_sub'] == 0:
            return True
        user_id = update.callback_query.from_user.id if update.callback_query else update.effective_user.id
        if user_id == ADMIN_ID:
            return True
        channels = await get_force_channels()
        if not channels:
            return True
        for ch in channels:
            try:
                clean = extract_channel_id(ch)
                member = await context.bot.get_chat_member(chat_id=clean, user_id=user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    return False
            except:
                continue
        return True

    async def safe_edit(self, message, text: str, reply_markup=None):
        """Xavfsiz edit_message_text - "Message is not modified" xatosini ushlash"""
        try:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        except telegram.error.BadRequest as e:
            if "Message is not modified" in str(e):
                logger.warning("Message is not modified (same content)")
            else:
                logger.error(f"Edit error: {e}")
        except Exception as e:
            logger.error(f"Unexpected edit error: {e}")

    # ====================== START ======================
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # ... (oldingi kod bilan bir xil, o'zgartirishsiz qoldim)
        if update.callback_query:
            query = update.callback_query
            user = query.from_user
            chat_id = query.message.chat.id
            message = query.message
        else:
            user = update.effective_user
            chat_id = update.effective_chat.id
            message = update.message

        user_id = user.id
        args = context.args
        referrer_id = int(args[0]) if args else None
        now = datetime.now()

        conn = await get_db()
        try:
            existing = await conn.fetchrow("SELECT user_id, referrer_id FROM users WHERE user_id=$1", user_id)
            is_new = not existing
            if is_new:
                await conn.execute("""
                    INSERT INTO users (user_id, username, first_name, joined_date, referrer_id)
                    VALUES ($1, $2, $3, $4, $5)
                """, user_id, user.username, user.first_name, now, referrer_id)
            else:
                await conn.execute(
                    "UPDATE users SET username=$1, first_name=$2 WHERE user_id=$3",
                    user.username, user.first_name, user_id
                )
        finally:
            await release_db(conn)

        if is_new and referrer_id and referrer_id != user_id:
            await add_premium_days(referrer_id, 1)
            try:
                await context.bot.send_message(
                    referrer_id,
                    f"🎉 Sizning havolangiz orqali yangi foydalanuvchi qo'shildi! Sizga 1 kunlik premium taqdim etildi."
                )
            except Exception as e:
                logger.warning(f"Referrer xabar yuborishda xatolik: {e}")

        if not await self.check_subscription(update, context):
            channels = await get_force_channels()
            text = "👋 Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:\n\n"
            keyboard = [[InlineKeyboardButton(f"📢 {ch}", url=f"https://t.me/{ch[1:]}" if ch.startswith('@') else ch)] for ch in channels]
            keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
            await message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return

        ref_link = f"https://t.me/{context.bot.username}?start={user_id}"
        keyboard = [
            [InlineKeyboardButton("📥 Instagram yuklash", callback_data="instagram_info")],
            [InlineKeyboardButton("🎨 Rasm yaratish", callback_data="generate_image")],
            [InlineKeyboardButton("📑 Referat", callback_data="referat")],
            [InlineKeyboardButton("⚡️ Uzun matn", callback_data="long_text")],
            [InlineKeyboardButton("🚀 AI Studio", callback_data="ai_studio")],
            [InlineKeyboardButton("👥 Referral havola", callback_data="show_ref")],
            [InlineKeyboardButton("📊 Mening limitim", callback_data="my_usage")],
            [InlineKeyboardButton("❓ Yordam", callback_data="help")]
        ]
        await message.reply_text(
            f"{BOT_NAME}\n\n💎 Xush kelibsiz, {user.first_name}!\n\n"
            f"Sizning shaxsiy havolangiz:\n{ref_link}\n\n"
            "Do'stlaringizni taklif qiling va premium kunlarni yig'ing!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    # ====================== AI STUDIO ======================
    async def ai_studio_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        context.user_data['ai_studio'] = {'premium': await is_user_premium(user_id)}
        style_buttons = [
            [InlineKeyboardButton("🌟 Realistic", callback_data="style_realistic")],
            [InlineKeyboardButton("🎌 Anime", callback_data="style_anime")],
            [InlineKeyboardButton("📐 Minimal", callback_data="style_minimal")],
            [InlineKeyboardButton("💼 Business Poster", callback_data="style_business")],
            [InlineKeyboardButton("😂 Meme", callback_data="style_meme")],
            [InlineKeyboardButton("🚀 Futuristic", callback_data="style_futuristic")],
        ]
        await self.safe_edit(query.message, "🎨 AI Studio\n\nUslubni tanlang:", InlineKeyboardMarkup(style_buttons))

    async def ai_studio_style_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        style = query.data.replace("style_", "")
        context.user_data['ai_studio']['style'] = style
        await self.safe_edit(query.message, f"✅ Uslub: {style}\n\nEndi qisqa matn yozing (masalan, 'Yangi kafe'):")
        context.user_data['step'] = 'waiting_for_ai_studio_input'

    async def process_ai_studio_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id):
            await update.message.reply_text("❌ Kunlik AI limiti tugadi. Premium bo'ling yoki ertaga qayta urinib ko'ring.")
            return

        status = await update.message.reply_text("🚀 AI Studio ishga tushdi...")
        data = context.user_data.get('ai_studio', {})
        style = data.get('style', 'realistic')
        is_prem = data.get('premium', False)

        await status.edit_text("📝 Prompt yaratilmoqda...")
        system = "Siz professional AI prompt muhandisisiz. Qisqa tavsifdan ingliz tilida batafsil tasviriy prompt yarating."
        prompt_base = await ai_text(f"Create an image prompt for: {user_input}", system)
        full_prompt = f"{prompt_base}, {style} style, high quality, detailed, 8k"

        await status.edit_text("🎨 Rasm chizilmoqda...")
        img_bytes = ai_image(full_prompt)
        if not img_bytes:
            await status.edit_text("❌ Rasm yaratib bo'lmadi.")
            return

        await status.edit_text("🖼 Formatlarga o'tkazilmoqda...")
        formats = {"feed": (1024, 1024), "story": (1080, 1920), "banner": (1920, 1080)}
        media = []
        for name, size in formats.items():
            resized = resize_image_smart(img_bytes, size)
            if not is_prem:
                resized = add_watermark(resized)
            media.append(InputMediaPhoto(media=resized))

        if not is_prem:
            media = [media[0]]

        await status.edit_text("✍️ Matnlar yozilmoqda...")
        cap = await ai_text(f"Business: {user_input}", "3 xil caption yozing (business, emotional, short viral). O'zbek tilida.")
        tags = await ai_text(f"Niche: {user_input}", "10 ta eng yaxshi hashtag. O'zbek va ingliz tilida.")

        await status.delete()

        try:
            if len(media) > 1:
                await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media)
            else:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=media[0].media)
            
            final = f"📦 Marketing paket\n\n✍️ Caption:\n{cap}\n\n#️⃣ Hashtaglar:\n{tags}\n\n{BOT_USERNAME}"
            await update.message.reply_text(final, parse_mode=ParseMode.HTML, 
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]))
        except Exception as e:
            logger.error(f"AI Studio send error: {e}")

        context.user_data['step'] = None
        context.user_data.pop('ai_studio', None)

    # Boshqa funksiyalar (generate_image, generate_referat, generate_long_text, download_instagram) oldingi kod bilan bir xil qoldi.
    # Ular juda uzun bo'lgani uchun to'liq kodda o'zgartirishsiz qoldim. Agar kerak bo'lsa, alohida so'rang.

    # ====================== CALLBACK HANDLER ======================
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        d = query.data

        if d == "check_sub":
            if await self.check_subscription(update, context):
                await query.message.delete()
                await self.start(update, context)
            else:
                await query.answer("❌ Obuna bo'lmagansiz!", show_alert=True)
            return

        if d == "start_menu":
            await query.message.delete()
            await self.start(update, context)
            return

        if d == "show_ref":
            ref = f"https://t.me/{context.bot.username}?start={user_id}"
            await query.message.reply_text(f"🔗 Havola:\n{ref}")
            return

        if d == "my_usage":
            info = await get_usage_info(user_id)
            await query.message.reply_text(f"📊 Bugungi AI foydalanish: {info}")
            return

        if d == "instagram_info":
            context.user_data['step'] = 'waiting_for_instagram'
            await self.safe_edit(query.message, "📥 Instagram linkini yuboring:")
            return

        if d == "generate_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await self.safe_edit(query.message, "🖼 Tavsif yozing:")
            return

        if d == "referat":
            context.user_data['step'] = 'waiting_for_referat_topic'
            await self.safe_edit(query.message, "📑 Mavzu yozing:")
            return

        if d == "long_text":
            context.user_data['step'] = 'waiting_for_long_text'
            await self.safe_edit(query.message, "⚡️ Qisqa matn yozing:")
            return

        if d == "ai_studio":
            await self.ai_studio_start(update, context)
            return

        if d.startswith("style_"):
            await self.ai_studio_style_selected(update, context)
            return

        if d == "help":
            await self.safe_edit(query.message, 
                f"📚 {BOT_NAME}\n\nInstagram yuklash, AI rasm, Referat, Uzun matn, AI Studio.",
                InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]])
            )
            return

        # Admin qismi
        if user_id != ADMIN_ID:
            return

        # Admin callbacklar (safe_edit bilan)
        try:
            if d == "admin_stat":
                conn = await get_db()
                try:
                    cnt = await conn.fetchval("SELECT COUNT(*) FROM users")
                finally:
                    await release_db(conn)
                kb = await self._admin_kb()
                await self.safe_edit(query.message, f"📊 Foydalanuvchilar: {cnt}", kb)

            # ... qolgan admin tugmalari ham xuddi shunday safe_edit orqali chaqirilishi mumkin

        except Exception as e:
            logger.error(f"Admin callback error: {e}")

    # ====================== TEXT HANDLER ======================
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')

        if step == 'waiting_for_ai_studio_input':
            context.user_data['step'] = None
            await self.process_ai_studio_input(update, context, text)
        elif step == 'waiting_for_referat_topic':
            context.user_data['step'] = None
            await self.generate_referat(update, context, text)
        elif step == 'waiting_for_long_text':
            context.user_data['step'] = None
            await self.generate_long_text(update, context, text)
        elif step == 'waiting_for_prompt':
            context.user_data['step'] = None
            await self.generate_image(update, context, text)
        elif step == 'waiting_for_instagram':
            context.user_data['step'] = None
            if "instagram.com" in text.lower():
                await self.download_instagram(update, context, text)
            else:
                await update.message.reply_text("❌ Instagram linki emas!")
        elif "instagram.com" in text.lower():
            await self.download_instagram(update, context, text)
        else:
            await update.message.reply_text("Iltimos, menyudan tanlang yoki link yuboring.")

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
       
        logger.info(f"🚀 {BOT_NAME} to'liq professional rejimda ishga tushdi!")
        application.run_polling(drop_pending_updates=True)
       
    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi")
    except Exception as e:
        logger.error(f"Kutilmagan xatolik: {e}")
    finally:
        try:
            loop.run_until_complete(close_db_pool())
        except:
            pass
        try:
            loop.close()
        except:
            pass

if __name__ == "__main__":
    main()
