import os
import asyncio
import logging
import uuid
import time
import hashlib
import re
from datetime import datetime, timedelta
from io import BytesIO
from typing import Optional, Dict, Any, List

import aiosqlite
import yt_dlp
import imageio_ffmpeg
import requests
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ParseMode
from telegram.error import BadRequest, TimedOut

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# ====================== LOGGING & ENV ======================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@zorekan_bot")
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "6698039974").split(",")]
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "")
DOWNLOAD_DIR = "downloads"
BOT_NAME = "🌟 Zo'r Ekan Bot"

openai_client = None
if OPENAI_API_KEY and OpenAI:
    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    AI_ACTIVE = "openai"
else:
    AI_ACTIVE = "pollinations"
    logger.warning("OpenAI API topilmadi. Pollinations (bepul) ishlatiladi.")

try:
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = "ffmpeg"

if not os.path.exists(DOWNLOAD_DIR):
    os.makedirs(DOWNLOAD_DIR)

# ====================== DATABASE (AIOSQLITE) ======================
DB_PATH = "users.db"

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            status TEXT DEFAULT 'active',
            is_premium INTEGER DEFAULT 0,
            premium_expire TEXT,
            joined_date TEXT,
            referrer_id INTEGER
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            channel_id TEXT,
            force_sub INTEGER DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS force_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id TEXT UNIQUE NOT NULL
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS user_daily_usage (
            user_id INTEGER,
            date TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS ai_cache (
            prompt_hash TEXT PRIMARY KEY,
            response TEXT,
            created INTEGER
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS prompt_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            category TEXT,
            system_prompt TEXT,
            user_prompt_template TEXT,
            is_premium INTEGER DEFAULT 0
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS premium_styles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            style_key TEXT UNIQUE,
            style_name TEXT,
            unlock_cost_days INTEGER DEFAULT 7
        )""")
        cur = await db.execute("SELECT COUNT(*) FROM settings")
        if (await cur.fetchone())[0] == 0:
            await db.execute("INSERT INTO settings (id, channel_id, force_sub) VALUES (1, '', 1)")
        cur = await db.execute("SELECT COUNT(*) FROM prompt_templates")
        if (await cur.fetchone())[0] == 0:
            templates = [
                ("Marketing Post", "business", "Siz professional marketologsiz. Berilgan mavzu bo'yicha ijodiy va sotuvchan post yozing.", "{topic}", 0),
                ("Referat Rejasi", "academic", "Siz akademik yordamchisiz. Mavzu bo'yicha batafsil referat rejasini o'zbek tilida tuzing.", "Mavzu: {topic}", 0),
                ("SEO Hashtaglar", "seo", "10 ta eng zo'r hashtaglarni qaytaring.", "Niche: {topic}", 0),
            ]
            for t in templates:
                await db.execute("INSERT INTO prompt_templates (name, category, system_prompt, user_prompt_template, is_premium) VALUES (?,?,?,?,?)", t)
        await db.commit()

# ====================== CACHE & UTILS ======================
class TTLCache:
    def __init__(self, ttl: int = 3600):
        self.cache: Dict[str, tuple[str, float]] = {}
        self.ttl = ttl

    def get(self, key: str) -> Optional[str]:
        if key in self.cache:
            value, timestamp = self.cache[key]
            if time.time() - timestamp < self.ttl:
                return value
            else:
                del self.cache[key]
        return None

    def set(self, key: str, value: str):
        self.cache[key] = (value, time.time())

ai_text_cache = TTLCache(ttl=3600)

def sanitize_input(text: str, max_len: int = 300) -> str:
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.replace('\n', ' ')
    if len(text) > max_len:
        text = text[:max_len]
    return text

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

# ====================== DB YORDAMCHI FUNKSIYALARI ======================
async def get_settings():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM settings WHERE id=1") as cur:
            row = await cur.fetchone()
    return row

async def get_force_channels():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT channel_id FROM force_channels") as cur:
            rows = await cur.fetchall()
    return [r[0] for r in rows]

async def add_force_channel(channel_id: str):
    channel_id = extract_channel_id(channel_id)
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO force_channels (channel_id) VALUES (?)", (channel_id,))
            await db.commit()
            return True
        except aiosqlite.IntegrityError:
            return False

async def remove_force_channel(channel_id: str):
    channel_id = extract_channel_id(channel_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM force_channels WHERE channel_id = ?", (channel_id,))
        await db.commit()

async def is_user_premium(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT is_premium, premium_expire FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return False
    if row[0]:
        if row[1]:
            try:
                expire = datetime.fromisoformat(row[1])
                if expire < datetime.now():
                    await asyncio.to_thread(_expire_premium_sync, user_id)
                    return False
            except:
                pass
        return True
    return False

def _expire_premium_sync(user_id: int):
    import sqlite3
    conn = sqlite3.connect(DB_PATH)
    conn.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()

async def add_premium_days(user_id: int, days: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT premium_expire FROM users WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        if row and row[0]:
            try:
                expire = datetime.fromisoformat(row[0])
            except:
                expire = datetime.now()
        else:
            expire = datetime.now()
        new_expire = max(expire, datetime.now()) + timedelta(days=days)
        await db.execute("UPDATE users SET is_premium=1, premium_expire=? WHERE user_id=?", (new_expire.isoformat(), user_id))
        await db.commit()

async def check_and_increment_usage(user_id: int, limit_free: int = 3, limit_premium: int = 50) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT count FROM user_daily_usage WHERE user_id=? AND date=?", (user_id, today)) as cur:
            row = await cur.fetchone()
        premium = await is_user_premium(user_id)
        limit = limit_premium if premium else limit_free
        current = row[0] if row else 0
        if current >= limit:
            return False
        if row:
            await db.execute("UPDATE user_daily_usage SET count=count+1 WHERE user_id=? AND date=?", (user_id, today))
        else:
            await db.execute("INSERT INTO user_daily_usage (user_id, date, count) VALUES (?,?,1)", (user_id, today))
        await db.commit()
        return True

async def get_usage_info(user_id: int) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT count FROM user_daily_usage WHERE user_id=? AND date=?", (user_id, today)) as cur:
            row = await cur.fetchone()
    current = row[0] if row else 0
    premium = await is_user_premium(user_id)
    limit = 50 if premium else 3
    return f"{current}/{limit}"

# ====================== AI SERVICES ======================
async def ai_text(prompt: str, system: Optional[str] = None, use_cache: bool = True) -> str:
    cache_key = f"{system}||{prompt}" if system else prompt
    if use_cache:
        cached = ai_text_cache.get(cache_key)
        if cached:
            return cached

    result = None
    if openai_client:
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = await asyncio.to_thread(
                openai_client.chat.completions.create,
                model="gpt-3.5-turbo",
                messages=messages,
                temperature=0.7,
                max_tokens=1000
            )
            result = resp.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"OpenAI text error: {e}")

    if not result and HUGGINGFACE_API_KEY:
        try:
            headers = {"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"}
            payload = {"inputs": f"{system}\n\n{prompt}" if system else prompt}
            resp = requests.post(
                "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.1",
                headers=headers, json=payload, timeout=30
            )
            if resp.status_code == 200:
                result = resp.json()[0]['generated_text']
        except Exception as e:
            logger.error(f"HuggingFace error: {e}")

    if not result:
        try:
            full = f"{system}\n\n{prompt}" if system else prompt
            url = f"https://text.pollinations.ai/{requests.utils.quote(full)}"
            resp = requests.get(url, timeout=60)
            if resp.status_code == 200:
                result = resp.text.strip()
        except Exception as e:
            logger.error(f"Pollinations error: {e}")

    if not result:
        result = "Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."

    if result and use_cache:
        ai_text_cache.set(cache_key, result)
    return result

def ai_image(prompt: str) -> Optional[bytes]:
    if openai_client:
        try:
            resp = openai_client.images.generate(prompt=prompt, n=1, size="1024x1024")
            url = resp.data[0].url
            img_data = requests.get(url, timeout=30).content
            return img_data
        except Exception as e:
            logger.error(f"OpenAI image error: {e}")

    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        resp = requests.get(url, timeout=120)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.error(f"Pollinations image error: {e}")

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
    def __init__(self):
        self.user_data_ttl = {}

    async def check_subscription(self, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
        settings = await get_settings()
        if not settings or settings[2] == 0:
            return True
        if user_id in ADMIN_IDS:
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
            except Exception as e:
                logger.warning(f"Kanal tekshirishda xato '{ch}': {e}")
                continue
        return True

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            query = update.callback_query
            user = query.from_user
            message = query.message
            if not message:
                await query.answer("Xabar topilmadi.", show_alert=True)
                return
            chat_id = message.chat.id
        else:
            user = update.effective_user
            message = update.message
            chat_id = update.effective_chat.id

        user_id = user.id
        args = context.args
        referrer_id = None
        if args:
            try:
                referrer_id = int(args[0])
            except:
                pass

        now = datetime.now().isoformat()

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id, username, first_name, joined_date, referrer_id) VALUES (?,?,?,?,?)",
                             (user_id, user.username, user.first_name, now, referrer_id))
            await db.commit()
            async with db.execute("SELECT changes() as c") as cur:
                changes = (await cur.fetchone())[0]
            is_new = changes > 0
            if not is_new:
                await db.execute("UPDATE users SET username=?, first_name=? WHERE user_id=?", (user.username, user.first_name, user_id))
                await db.commit()

        if is_new and referrer_id and referrer_id != user_id:
            await add_premium_days(referrer_id, 1)
            try:
                await context.bot.send_message(referrer_id, "🎉 Sizning havolangiz orqali yangi foydalanuvchi qo'shildi! 1 kunlik premium taqdim etildi.")
            except Exception as e:
                logger.warning(f"Referrerga xabar yuborib bo'lmadi: {e}")

        if not await self.check_subscription(user_id, context):
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
        self._clear_user_data(context)

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
        style = query.data.replace("style_", "")
        context.user_data['ai_studio']['style'] = style
        await query.message.edit_text(f"✅ Uslub: {style}\nEndi qisqa matn yozing (masalan, 'Yangi kafe'):")
        context.user_data['step'] = 'waiting_for_ai_studio_input'
        self._update_user_ttl(update.effective_user.id)

    async def process_ai_studio_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        user_id = update.effective_user.id
        user_input = sanitize_input(user_input)
        if not await check_and_increment_usage(user_id):
            await update.message.reply_text("❌ Kunlik AI limiti tugadi. Premium bo'ling yoki ertaga qayta urinib ko'ring.")
            return self._clear_user_data(context)

        status = await update.message.reply_text("🚀 AI Studio ishga tushdi...")
        data = context.user_data.get('ai_studio', {})
        style = data.get('style', 'realistic')
        is_prem = data.get('premium', False)

        try:
            await status.edit_text("📝 Prompt yaratilmoqda...")
            system = "Siz professional AI prompt muhandisisiz. Qisqa tavsifdan ingliz tilida batafsil tasviriy prompt yarating."
            prompt_base = await ai_text(f"Create an image prompt for: {user_input}", system)
            full_prompt = f"{prompt_base}, {style} style"

            await status.edit_text("🎨 Rasm chizilmoqda...")
            img_bytes = ai_image(full_prompt)
            if not img_bytes:
                raise Exception("Rasm generatsiyasi muvaffaqiyatsiz")

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
            if len(media) > 1:
                await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media)
            else:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=media[0].media)
            final = f"<b>📦 Marketing paket</b>\n\n✍️ Caption:\n{cap}\n\n#️⃣ Hashtaglar:\n{tags}\n\n{BOT_USERNAME}"
            await update.message.reply_text(final, parse_mode=ParseMode.HTML,
                                           reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]))
        except Exception as e:
            logger.error(f"AI Studio error: {e}")
            await status.edit_text("❌ Xatolik yuz berdi. Keyinroq urinib ko'ring.")
        finally:
            self._clear_user_data(context)

    async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id):
            await update.message.reply_text("❌ Kunlik limit tugadi.")
            return self._clear_user_data(context)
        prompt = sanitize_input(prompt)
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
        self._clear_user_data(context)

    async def generate_referat(self, update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id): return self._clear_user_data(context)
        topic = sanitize_input(topic)
        status = await update.message.reply_text("📑 Tayyorlanmoqda...")
        result = await ai_text(f"Mavzu: {topic}", "Akademik referat rejasi tuzing. O'zbek tilida.")
        await status.edit_text(f"📑 {topic}\n\n{result}\n\n{BOT_USERNAME}", parse_mode=ParseMode.HTML)
        self._clear_user_data(context)

    async def generate_long_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, short_text: str):
        user_id = update.effective_user.id
        if not await check_and_increment_usage(user_id): return self._clear_user_data(context)
        short_text = sanitize_input(short_text)
        status = await update.message.reply_text("⚡️ Matn kengaytirilmoqda...")
        result = await ai_text(f"Qisqa matn: {short_text}", "Qisqa fikrni 200-300 so'zga kengaytir. O'zbek tilida.")
        await status.edit_text(f"📝 Kengaytirilgan matn:\n\n{result}\n\n{BOT_USERNAME}", parse_mode=ParseMode.HTML)
        self._clear_user_data(context)

    async def download_instagram(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
        user_id = update.effective_user.id
        if not await self.check_subscription(user_id, context):
            await self.start(update, context)
            return
        status = await update.message.reply_text("⚡️ Tahlil...")
        unique_id = f"{uuid.uuid4()}"
        fpath = os.path.join(DOWNLOAD_DIR, f"{unique_id}.mp4")
        ydl_opts = {
            'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best',
            'outtmpl': fpath,
            'merge_output_format': 'mp4',
            'ffmpeg_location': FFMPEG_PATH,
            'quiet': True,
            'noplaylist': True,
            'max_filesize': 50*1024*1024,
            'socket_timeout': 180,
            'retries': 3,
        }
        try:
            await status.edit_text("⏳ Yuklanmoqda...")
            loop = asyncio.get_running_loop()
            def dl(): 
                with yt_dlp.YoutubeDL(ydl_opts) as ydl: ydl.download([url])
            await asyncio.wait_for(loop.run_in_executor(None, dl), timeout=300)
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
        self._clear_user_data(context)

    async def admin_panel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:
            await update.message.reply_text("⛔ Siz admin emassiz!")
            return
        settings = await get_settings()
        sub_status = "✅ YOQIQ" if settings[2] == 1 else "❌ O'CHIQ"
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
        if update.effective_user.id not in ADMIN_IDS: return
        if not update.message.reply_to_message:
            await update.message.reply_text("Reply qiling.")
            return
        status = await update.message.reply_text("📢 Yuborilmoqda...")
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT user_id FROM users") as cur:
                users = await cur.fetchall()
        sent = failed = 0
        for u in users:
            try:
                await context.bot.copy_message(u[0], update.effective_chat.id, update.message.reply_to_message.message_id)
                sent += 1
                await asyncio.sleep(0.1)
            except Exception as e:
                failed += 1
        await status.edit_text(f"✅ Yetkazildi: {sent}\n❌ Bloklagan: {failed}")

    def _admin_kb(self, settings):
        sub_status = "✅ YOQIQ" if settings[2] == 1 else "❌ O'CHIQ"
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Statistika", callback_data="admin_stat")],
            [InlineKeyboardButton(f"Majburiy obuna: {sub_status}", callback_data="toggle_sub")],
            [InlineKeyboardButton("➕ Kanal qo'shish", callback_data="add_channel")],
            [InlineKeyboardButton("➖ Kanal o'chirish", callback_data="remove_channel")],
            [InlineKeyboardButton("📋 Kanallar ro'yxati", callback_data="list_channels")],
            [InlineKeyboardButton("📢 Reklama yuborish", callback_data="send_help")]
        ])

    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        await query.answer()
        d = query.data

        if d == "check_sub":
            if await self.check_subscription(user_id, context):
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
        elif user_id in ADMIN_IDS:
            await self._admin_callback(update, context)
        else:
            await query.answer("Bu amal siz uchun mavjud emas.", show_alert=True)

    async def _admin_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        d = query.data
        settings = await get_settings()
        if d == "admin_stat":
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT COUNT(*) FROM users") as cur:
                    cnt = (await cur.fetchone())[0]
            await query.message.edit_text(f"📊 Foydalanuvchilar: {cnt}", reply_markup=self._admin_kb(settings))
        elif d == "toggle_sub":
            async with aiosqlite.connect(DB_PATH) as db:
                await db.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id=1")
                await db.commit()
            settings = await get_settings()
            await query.message.edit_text("🕹 Admin paneli", reply_markup=self._admin_kb(settings))
        elif d == "add_channel":
            context.user_data['step'] = 'add_force_channel'
            await query.message.edit_text("➕ Kanal username yoki havolasini yuboring:",
                                         reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel")]]))
        elif d == "remove_channel":
            context.user_data['step'] = 'remove_force_channel'
            chs = await get_force_channels()
            txt = "➖ O'chirish uchun kanalni yuboring:\n\n" + "\n".join(chs) if chs else "Ro'yxat bo'sh."
            await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel")]]))
        elif d == "list_channels":
            chs = await get_force_channels()
            txt = "📋 Kanallar:\n" + "\n".join(chs) if chs else "Ro'yxat bo'sh."
            await query.message.edit_text(txt, reply_markup=self._admin_kb(settings))
        elif d == "send_help":
            await query.message.edit_text("📢 Reklama: xabarga reply qilib /send", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Orqaga", callback_data="admin_panel")]]))
        elif d == "admin_panel":
            await query.message.edit_text("🕹 Admin paneli", reply_markup=self._admin_kb(settings))
        elif d == "cancel":
            context.user_data['step'] = None
            await query.message.edit_text("🕹 Admin paneli", reply_markup=self._admin_kb(settings))

    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')

        if user_id in ADMIN_IDS:
            if step == 'add_force_channel':
                if await add_force_channel(text):
                    await update.message.reply_text(f"✅ Qo'shildi: {extract_channel_id(text)}", reply_markup=self._admin_kb(await get_settings()))
                else:
                    await update.message.reply_text("⚠️ Mavjud.", reply_markup=self._admin_kb(await get_settings()))
                context.user_data['step'] = None
                return
            if step == 'remove_force_channel':
                await remove_force_channel(text)
                await update.message.reply_text(f"✅ O'chirildi: {extract_channel_id(text)}", reply_markup=self._admin_kb(await get_settings()))
                context.user_data['step'] = None
                return

        if step == 'waiting_for_ai_studio_input':
            await self.process_ai_studio_input(update, context, text)
        elif step == 'waiting_for_referat_topic':
            await self.generate_referat(update, context, text)
        elif step == 'waiting_for_long_text':
            await self.generate_long_text(update, context, text)
        elif step == 'waiting_for_prompt':
            await self.generate_image(update, context, text)
        elif step == 'waiting_for_instagram':
            if "instagram.com" in text.lower():
                await self.download_instagram(update, context, text)
            else:
                await update.message.reply_text("❌ Instagram linki emas!")
                self._clear_user_data(context)
        elif "instagram.com" in text.lower():
            await self.download_instagram(update, context, text)
        else:
            await update.message.reply_text("Iltimos, menyudan tanlang yoki link yuboring.")
            self._clear_user_data(context)

    def _clear_user_data(self, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()

    def _update_user_ttl(self, user_id: int):
        self.user_data_ttl[user_id] = time.time()

# ====================== ASOSIY DASTUR (EVENT LOOP BILAN TO'G'RI ISHLOVCHI) ======================
async def async_main():
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
    # To'g'ridan-to'g'ri asyncio.run ishlatamiz.
    # Agar muhitda event loop muammosi bo'lsa, qo'lda loop yaratamiz.
    try:
        asyncio.run(async_main())
    except RuntimeError as e:
        if "event loop" in str(e).lower():
            # Yangi event loop yaratib, uni joriy qilamiz
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(async_main())
        else:
            raise
