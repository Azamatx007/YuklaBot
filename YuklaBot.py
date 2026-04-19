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
from telegram.error import BadRequest
import asyncpg
import yt_dlp

try:
    import openai
except ImportError:
    openai = None

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
DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL ulanish URL
DOWNLOAD_DIR = "downloads"
BOT_NAME = "🌟 Zo'r Ekan Bot"

if OPENAI_API_KEY and openai:
    openai.api_key = OPENAI_API_KEY
    AI_ACTIVE = "openai"
else:
    AI_ACTIVE = "pollinations"
    logger.warning("OpenAI API key topilmadi. Pollinations (bepul) ishlatiladi.")

try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ====================== DATABASE POOL ======================
db_pool = None

async def init_db_pool():
    """PostgreSQL ulanishlar hovuzini yaratish."""
    global db_pool
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60
    )
    logger.info("PostgreSQL pool yaratildi")

async def close_db_pool():
    """Hovuzni yopish."""
    global db_pool
    if db_pool:
        await db_pool.close()
        logger.info("PostgreSQL pool yopildi")

async def get_db():
    """Hovuzdan ulanish olish."""
    return await db_pool.acquire()

async def release_db(conn):
    """Ulanishni qaytarish."""
    await db_pool.release(conn)

# ====================== DATABASE INIT ======================
async def init_db():
    """PostgreSQL jadvallarini yaratish."""
    conn = await get_db()
    try:
        # users jadvali
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
        # settings jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                channel_id TEXT,
                force_sub INTEGER DEFAULT 0
            )
        """)
        # force_channels jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS force_channels (
                id SERIAL PRIMARY KEY,
                channel_id TEXT UNIQUE NOT NULL
            )
        """)
        # user_daily_usage jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS user_daily_usage (
                user_id BIGINT,
                date TEXT,
                count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, date)
            )
        """)
        # ai_cache jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_cache (
                prompt_hash TEXT PRIMARY KEY,
                response TEXT,
                created INTEGER
            )
        """)
        # prompt_templates jadvali
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
        # premium_styles jadvali
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS premium_styles (
                id SERIAL PRIMARY KEY,
                style_key TEXT UNIQUE,
                style_name TEXT,
                unlock_cost_days INTEGER DEFAULT 7
            )
        """)

        # Boshlang'ich ma'lumotlarni kiritish
        row = await conn.fetchrow("SELECT COUNT(*) FROM settings")
        if row['count'] == 0:
            await conn.execute("INSERT INTO settings (id, channel_id, force_sub) VALUES (1, '', 1)")

        row = await conn.fetchrow("SELECT COUNT(*) FROM prompt_templates")
        if row['count'] == 0:
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
    finally:
        await release_db(conn)

# ====================== YORDAMCHI FUNKSIYALAR ======================
def extract_channel_id(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith('@'): return raw
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
        await conn.execute(
            "INSERT INTO force_channels (channel_id) VALUES ($1)",
            channel_id
        )
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
        row = await conn.fetchrow("SELECT * FROM settings WHERE id=1")
        return row
    finally:
        await release_db(conn)

async def is_user_premium(user_id: int) -> bool:
    conn = await get_db()
    try:
        row = await conn.fetchrow(
            "SELECT is_premium, premium_expire FROM users WHERE user_id=$1",
            user_id
        )
        if not row: return False
        if row['is_premium']:
            if row['premium_expire']:
                expire = row['premium_expire']
                if expire < datetime.now():
                    # Premium muddati tugagan – orqa planda yangilash
                    async def expire_premium():
                        c = await get_db()
                        try:
                            await c.execute("UPDATE users SET is_premium=FALSE WHERE user_id=$1", user_id)
                        finally:
                            await release_db(c)
                    asyncio.create_task(expire_premium())
                    return False
            return True
        return False
    finally:
        await release_db(conn)

async def add_premium_days(user_id: int, days: int):
    conn = await get_db()
    try:
        row = await conn.fetchrow("SELECT premium_expire FROM users WHERE user_id=$1", user_id)
        if row and row['premium_expire']:
            expire = row['premium_expire']
        else:
            expire = datetime.now()
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
        row = await conn.fetchrow(
            "SELECT count FROM user_daily_usage WHERE user_id=$1 AND date=$2",
            user_id, today
        )
        premium = await is_user_premium(user_id)
        limit = limit_premium if premium else limit_free
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

# ====================== AI CACHE ======================
cache_ttl = 3600

async def get_cached(prompt: str) -> str | None:
    h = hashlib.md5(prompt.encode()).hexdigest()
    conn = await get_db()
    try:
        row = await conn.fetchrow(
            "SELECT response, created FROM ai_cache WHERE prompt_hash=$1",
            h
        )
        if row:
            if time.time() - row['created'] < cache_ttl:
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

# ====================== AI GENERATSIYA ======================
async def ai_text(prompt: str, system: str = None, use_cache: bool = True) -> str:
    cache_key = f"{system}||{prompt}" if system else prompt
    if use_cache:
        cached = await get_cached(cache_key)
        if cached: return cached

    result = None
    if AI_ACTIVE == "openai" and openai:
        try:
            messages = []
            if system: messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=messages, temperature=0.7, max_tokens=1000)
            result = resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI text error: {e}")

    if not result:
        full = f"{system}\n\n{prompt}" if system else prompt
        try:
            url = f"https://text.pollinations.ai/{requests.utils.quote(full)}"
            r = requests.get(url, timeout=60)
            if r.status_code == 200:
                result = r.text.strip()
        except: pass

    if not result:
        result = "Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

    if result and use_cache:
        await set_cache(cache_key, result)
    return result

def ai_image(prompt: str) -> bytes | None:
    if AI_ACTIVE == "openai" and openai:
        try:
            resp = openai.Image.create(prompt=prompt, n=1, size="1024x1024")
            url = resp['data'][0]['url']
            img_data = requests.get(url, timeout=30).content
            return img_data
        except Exception as e:
            logger.error(f"OpenAI image error: {e}")

    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        r = requests.get(url, timeout=120)
        if r.status_code == 200:
            return r.content
    except: pass
    return None

# ====================== RASMGA ISHLOV ======================
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
        txt = Image.new('RGBA', img.size, (255,255,255,0))
        draw = ImageDraw.Draw(txt)
        try:
            font = ImageFont.truetype("arial.ttf", size=int(img.width * 0.05))
        except:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0,0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pos = (img.width - tw - 20, img.height - th - 20)
        draw.text(pos, text, font=font, fill=(255,255,255,180))
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
        if settings['force_sub'] == 0: return True
        if update.callback_query:
            user_id = update.callback_query.from_user.id
        else:
            user_id = update.effective_user.id
        if user_id == ADMIN_ID: return True
        channels = await get_force_channels()
        if not channels: return True
        for ch in channels:
            try:
                clean = extract_channel_id(ch)
                member = await context.bot.get_chat_member(chat_id=clean, user_id=user_id)
                if member.status not in ['member', 'administrator', 'creator']:
                    return False
            except: continue
        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            query = update.callback_query
            effective_user = query.from_user
            chat_id = query.message.chat.id
            message = query.message
        else:
            effective_user = update.effective_user
            chat_id = update.effective_chat.id
            message = update.message

        user = effective_user
        user_id = user.id
        args = context.args
        referrer_id = None
        if args:
            try:
                referrer_id = int(args[0])
            except:
                pass

        now = datetime.now()

        # Foydalanuvchini bazaga qo'shish / yangilash
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

        # Yangi foydalanuvchi va referrer mavjud bo'lsa, referrerga bonus berish
        if is_new and referrer_id and referrer_id != user_id:
            await add_premium_days(referrer_id, 1)
            try:
                await context.bot.send_message(
                    referrer_id,
                    f"🎉 Sizning havolangiz orqali yangi foydalanuvchi qo'shildi! Sizga 1 kunlik premium taqdim etildi."
                )
            except Exception as e:
                logger.warning(f"Referrer {referrer_id} ga xabar yuborib bo'lmadi: {e}")

        # Majburiy obuna tekshiruvi
        if not await self.check_subscription(update, context):
            channels = await get_force_channels()
            text = "👋 <b>Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:</b>\n\n"
            keyboard = []
            for ch in channels:
                if ch.startswith('@'):
                    link = f"https://t.me/{ch[1:]}"
                    btn = f"📢 {ch}"
                else:
                    link = ch
                    btn = "📢 Kanal"
                keyboard.append([InlineKeyboardButton(btn, url=link)])
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
            f"Sizning shaxsiy havolangiz:\n<code>{ref_link}</code>\n\n"
            "Do'stlaringizni taklif qiling va premium kunlarni yig'ing!",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    # ---------- AI STUDIO ----------
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
        await query.message.edit_text("<b>🎨 AI Studio</b>\n\nUslubni tanlang:", reply_markup=InlineKeyboardMarkup(style_buttons), parse_mode=ParseMode.HTML)

    async def ai_studio_style_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        data = query.data
        style = data.replace("style_", "")
        context.user_data['ai_studio']['style'] = style
        await query.message.edit_text(f"✅ Uslub: {style}\nEndi qisqa matn yozing (masalan, 'Yangi kafe'):")
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
        full_prompt = f"{prompt_base}, {style} style"

        await status.edit_text("🎨 Rasm chizilmoqda...")
        img_bytes = ai_image(full_prompt)
        if not img_bytes:
            await status.edit_text("❌ Rasm yaratib bo'lmadi.")
            return

        await status.edit_text("🖼 Formatlarga o'tkazilmoqda...")
        formats = {"feed": (1024,1024), "story": (1080,1920), "banner": (1920,1080)}
        media = []
        for name, size in formats.items():
            resized = resize_image_smart(img_bytes, size)
            if not is_prem:
                resized = add_watermark(resized)
            media.append(InputMediaPhoto(media=resized))
        if not is_prem:
            media = [media[0]]

        await status.edit_text("✍️ Matnlar yozilmoqda...")
        cap = await ai_text(f"Business: {user_input}", "3 xil caption yozing (business, emotional, short viral).")
        tags = await ai_text(f"Niche: {user_input}", "10 ta eng yaxshi hashtag.")

        await status.delete()
        try:
            if len(media) > 1:
                await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media)
            else:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=media[0].media)
            final = f"<b>📦 Marketing paket</b>\n\n✍️ Caption:\n{cap}\n\n#️⃣ Hashtaglar:\n{tags}\n\n{BOT_USERNAME}"
            await update.message.reply_text(final, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]))
        except Exception as e:
            logger.error(f"AI Studio send error: {e}")
        context.user_data['step'] = None

    # ---------- ODDIY RASM YARATISH ----------
    async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id):
            await update.message.reply_text("❌ Kunlik limit tugadi.")
            return
        status = await update.message.reply_text("🎨 Rasm yaratilmoqda...")
        img = ai_image(prompt)
        if img:
            path = f"{DOWNLOAD_DIR}/{uuid.uuid4()}.jpg"
            with open(path, 'wb') as f: f.write(img)
            cap = f"✨ AI rasm\n📝 {prompt}\n\n{BOT_USERNAME}"
            with open(path, 'rb') as ph:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=ph, caption=cap, parse_mode=ParseMode.HTML)
            os.remove(path)
            await status.delete()
        else:
            await status.edit_text("❌ Xatolik.")

    # ---------- REFERAT & UZUN MATN ----------
    async def generate_referat(self, update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id): return
        status = await update.message.reply_text("📑 Tayyorlanmoqda...")
        result = await ai_text(f"Mavzu: {topic}", "Akademik referat rejasi tuzing. O'zbek tilida.")
        await status.edit_text(f"📑 {topic}\n\n{result}\n\n{BOT_USERNAME}", parse_mode=ParseMode.HTML)

    async def generate_long_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, short_text: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id): return
        status = await update.message.reply_text("⚡️ Matn kengaytirilmoqda...")
        result = await ai_text(f"Qisqa matn: {short_text}", "Qisqa fikrni 200-300 so'zga kengaytir. O'zbek tilida.")
        await status.edit_text(f"📝 Kengaytirilgan matn:\n\n{result}\n\n{BOT_USERNAME}", parse_mode=ParseMode.HTML)

    # ---------- INSTAGRAM YUKLASH ----------
    async def download_instagram(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
        user_id = update.effective_user.id
        if not await self.check_subscription(update, context):
            await self.start(update, context)
            return
        status = await update.message.reply_text("⚡️ Tahlil...")
        fpath = os.path.join(DOWNLOAD_DIR, f"{uuid.uuid4()}.mp4")
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'outtmpl': fpath,
            'merge_output_format': 'mp4',
            'ffmpeg_location': FFMPEG_PATH,
            'quiet': True,
            'noplaylist': True,
            'max_filesize': 50*1024*1024,
            'socket_timeout': 120,
        }
        try:
            await status.edit_text("⏳ Yuklanmoqda...")
            loop = asyncio.get_running_loop()
            def dl(): 
                with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
            await asyncio.wait_for(loop.run_in_executor(None, dl), timeout=180)
            if os.path.exists(fpath) and os.path.getsize(fpath)>0:
                await status.edit_text("📤 Yuborilmoqda...")
                cap = f"🎬 Video yuklandi!\n\n{BOT_USERNAME}\n💾 {os.path.getsize(fpath)/(1024*1024):.1f} MB"
                await context.bot.send_video(chat_id=update.effective_chat.id, video=open(fpath,'rb'), caption=cap, supports_streaming=True, parse_mode=ParseMode.HTML)
                await status.delete()
            else:
                await status.edit_text("❌ Yuklab bo'lmadi.")
        except Exception as e:
            logger.error(f"Download error: {e}")
            await status.edit_text("❌ Xatolik.")
        finally:
            if os.path.exists(fpath):
                try: os.remove(fpath)
                except: pass

    # ---------- ADMIN PANEL ----------
    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("⛔ Siz admin emassiz!")
            return
        settings = await get_settings()
        sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Statistika", callback_data="admin_stat")],
            [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
            [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="add_channel")],
            [InlineKeyboardButton("➖ Kanal o'chirish", callback_data="remove_channel")],
            [InlineKeyboardButton("📋 Kanallar ro'yxati", callback_data="list_channels")],
            [InlineKeyboardButton("📢 Reklama yuborish", callback_data="send_help")]
        ])
        await update.message.reply_text("🕹 Admin paneli", reply_markup=kb, parse_mode=ParseMode.HTML)

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        if not update.message.reply_to_message:
            await update.message.reply_text("Reply qiling.")
            return
        status = await update.message.reply_text("📢 Yuborilmoqda...")
        conn = await get_db()
        try:
            users = await conn.fetch("SELECT user_id FROM users")
        finally:
            await release_db(conn)
        sent = failed = 0
        for u in users:
            try:
                await context.bot.copy_message(u['user_id'], update.effective_chat.id, update.message.reply_to_message.message_id)
                sent += 1
                await asyncio.sleep(0.05)
            except: failed += 1
        await status.edit_text(f"✅ Yetkazildi: {sent}\n❌ Bloklagan: {failed}")

    async def _admin_kb(self):
        settings = await get_settings()
        sub_status = "✅ YOQIQ" if settings['force_sub'] == 1 else "❌ O'CHIQ"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Statistika", callback_data="admin_stat")],
            [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
            [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="add_channel")],
            [InlineKeyboardButton("➖ Kanal o'chirish", callback_data="remove_channel")],
            [InlineKeyboardButton("📋 Kanallar ro'yxati", callback_data="list_channels")],
            [InlineKeyboardButton("📢 Reklama yuborish", callback_data="send_help")]
        ])

    # ---------- CALLBACK HANDLER ----------
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
        elif d == "start_menu":
            await query.message.delete()
            await self.start(update, context)
        elif d == "show_ref":
            ref = f"https://t.me/{context.bot.username}?start={user_id}"
            await query.message.reply_text(f"🔗 Havola:\n<code>{ref}</code>", parse_mode=ParseMode.HTML)
        elif d == "my_usage":
            info = await get_usage_info(user_id)
            await query.message.reply_text(f"📊 Bugungi AI foydalanish: {info}")
        elif d == "instagram_info":
            context.user_data['step'] = 'waiting_for_instagram'
            await query.message.edit_text("📥 Instagram linkini yuboring:")
        elif d == "generate_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await query.message.edit_text("🖼 Tavsif yozing:")
        elif d == "referat":
            context.user_data['step'] = 'waiting_for_referat_topic'
            await query.message.edit_text("📑 Mavzu yozing:")
        elif d == "long_text":
            context.user_data['step'] = 'waiting_for_long_text'
            await query.message.edit_text("⚡️ Qisqa matn yozing:")
        elif d == "ai_studio":
            await self.ai_studio_start(update, context)
        elif d.startswith("style_"):
            await self.ai_studio_style_selected(update, context)
        elif d == "help":
            help_text = f"📚 {BOT_NAME}\n\nInstagram yuklash, AI rasm, Referat, Uzun matn, AI Studio."
            await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]), parse_mode=ParseMode.HTML)

        # --- Admin tugmalari ---
        if user_id != ADMIN_ID: return
        try:
            if d == "admin_stat":
                conn = await get_db()
                try:
                    cnt = await conn.fetchval("SELECT COUNT(*) FROM users")
                finally:
                    await release_db(conn)
                kb = await self._admin_kb()
                await query.message.edit_text(f"📊 Foydalanuvchilar: {cnt}", reply_markup=kb)
            elif d == "toggle_sub":
                conn = await get_db()
                try:
                    await conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id=1")
                finally:
                    await release_db(conn)
                kb = await self._admin_kb()
                await query.message.edit_text("🕹 Admin paneli", reply_markup=kb)
            elif d == "add_channel":
                context.user_data['step'] = 'add_force_channel'
                await query.message.edit_text("➕ Kanal username yoki havolasini yuboring:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel")]]))
            elif d == "remove_channel":
                context.user_data['step'] = 'remove_force_channel'
                chs = await get_force_channels()
                txt = "➖ O'chirish uchun kanalni yuboring:\n\n" + "\n".join(chs) if chs else "Ro'yxat bo'sh."
                await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel")]]))
            elif d == "list_channels":
                chs = await get_force_channels()
                txt = "📋 Kanallar:\n" + "\n".join(chs) if chs else "Ro'yxat bo'sh."
                kb = await self._admin_kb()
                await query.message.edit_text(txt, reply_markup=kb)
            elif d == "send_help":
                await query.message.edit_text("📢 Reklama: xabarga reply qilib /send", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Orqaga", callback_data="admin_panel")]]))
            elif d == "admin_panel":
                kb = await self._admin_kb()
                await query.message.edit_text("🕹 Admin paneli", reply_markup=kb)
            elif d == "cancel":
                context.user_data['step'] = None
                kb = await self._admin_kb()
                await query.message.edit_text("🕹 Admin paneli", reply_markup=kb)
        except Exception as e:
            logger.error(f"Admin callback error: {e}")

    # ---------- TEXT HANDLER ----------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')

        if user_id == ADMIN_ID:
            if step == 'add_force_channel':
                if await add_force_channel(text):
                    kb = await self._admin_kb()
                    await update.message.reply_text(f"✅ Qo'shildi: {extract_channel_id(text)}", reply_markup=kb)
                else:
                    kb = await self._admin_kb()
                    await update.message.reply_text("⚠️ Mavjud.", reply_markup=kb)
                context.user_data['step'] = None
                return
            if step == 'remove_force_channel':
                await remove_force_channel(text)
                kb = await self._admin_kb()
                await update.message.reply_text(f"✅ O'chirildi: {extract_channel_id(text)}", reply_markup=kb)
                context.user_data['step'] = None
                return

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

# ====================== ASOSIY DASTUR ======================
async def main():
    await init_db_pool()
    await init_db()

    bot = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("admin", bot.admin_panel))
    app.add_handler(CommandHandler("send", bot.broadcast_send))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))

    print(f"🚀 {BOT_NAME} to'liq professional rejimda ishga tushdi!")
    await app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot to'xtatildi")
    finally:
        asyncio.run(close_db_pool())
