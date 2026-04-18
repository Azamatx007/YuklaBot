import os
import asyncio
import logging
import sqlite3
import yt_dlp
import uuid
import imageio_ffmpeg
import requests
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

# ====================== LOGGING & ENV ======================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "@zorekan_bot")
ADMIN_ID = 6698039974
DOWNLOAD_DIR = "downloads"
BOT_NAME = "🌟 Zo'r Ekan Bot"

# ====================== FFMPEG ======================
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
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, status TEXT DEFAULT 'active', is_premium INTEGER DEFAULT 0)")
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

def add_force_channel(channel_id: str):
    channel_id = extract_channel_id(channel_id)
    conn = get_db_connection()
    try:
        conn.execute("INSERT INTO force_channels (channel_id) VALUES (?)", (channel_id,))
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def remove_force_channel(channel_id: str):
    channel_id = extract_channel_id(channel_id)
    conn = get_db_connection()
    conn.execute("DELETE FROM force_channels WHERE channel_id = ?", (channel_id,))
    conn.commit()
    conn.close()

def is_user_premium(user_id: int) -> bool:
    conn = get_db_connection()
    row = conn.execute("SELECT is_premium FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row and row['is_premium'] == 1

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

# ====================== MAJBURIY OBUNA ======================
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
            clean_id = extract_channel_id(ch_id)
            member = await context.bot.get_chat_member(chat_id=clean_id, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except Exception as e:
            logger.warning(f"Kanal tekshirishda xato '{ch_id}': {e}")
            continue
    return True

# ====================== AI YORDAMCHI FUNKSIYALAR ======================
STYLES = {
    "realistic": "realistic photography",
    "anime": "anime style illustration",
    "minimal": "minimalist design, clean and simple",
    "business": "professional business poster style",
    "meme": "funny meme style, viral internet culture",
    "futuristic": "futuristic tech, neon lights, cyberpunk"
}

def generate_ai_text(prompt: str, system: str = None) -> str:
    try:
        full = f"{system}\n\n{prompt}" if system else prompt
        url = f"https://text.pollinations.ai/{requests.utils.quote(full)}"
        resp = requests.get(url, timeout=60)
        return resp.text.strip() if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"AI Text error: {e}")
        return None

def generate_prompt_from_input(user_input: str) -> str:
    system = "Siz professional AI prompt muhandisisiz. Qisqa tavsifdan marketing poster uchun ingliz tilida batafsil prompt yarating."
    return generate_ai_text(f"Create an image prompt for: {user_input}", system) or user_input

def generate_image_from_prompt(prompt: str) -> bytes:
    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        resp = requests.get(url, timeout=120)
        return resp.content if resp.status_code == 200 else None
    except Exception as e:
        logger.error(f"Image gen error: {e}")
        return None

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
        output = BytesIO()
        img.save(output, format='JPEG', quality=90)
        return output.getvalue()
    except Exception as e:
        logger.error(f"Resize error: {e}")
        return image_bytes

def add_watermark(image_bytes: bytes, text: str = BOT_USERNAME) -> bytes:
    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
        txt_layer = Image.new('RGBA', img.size, (255,255,255,0))
        draw = ImageDraw.Draw(txt_layer)
        try:
            font = ImageFont.truetype("arial.ttf", size=int(img.width * 0.05))
        except:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0,0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        pos = (img.width - tw - 20, img.height - th - 20)
        draw.text(pos, text, font=font, fill=(255,255,255,180))
        combined = Image.alpha_composite(img, txt_layer).convert("RGB")
        out = BytesIO()
        combined.save(out, format='JPEG', quality=85)
        return out.getvalue()
    except Exception as e:
        logger.error(f"Watermark error: {e}")
        return image_bytes

# ====================== BOT KLASSI ======================
class InstagramDownloader:

    # ---------- START ----------
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
                else:
                    link = f"https://t.me/{ch}" if not ch.startswith('https') else ch
                    btn_text = "📢 Kanalga o'tish"
                keyboard.append([InlineKeyboardButton(btn_text, url=link)])
            keyboard.append([InlineKeyboardButton("✅ Tekshirish", callback_data="check_sub")])
            await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
            return

        keyboard = [
            [InlineKeyboardButton("📥 Instagramdan yuklash", callback_data="instagram_info")],
            [InlineKeyboardButton("🎨 Rasm yaratish (AI)", callback_data="generate_image")],
            [InlineKeyboardButton("📑 Referat tayyorlash", callback_data="referat")],
            [InlineKeyboardButton("⚡️ Uzun matn yozish", callback_data="long_text")],
            [InlineKeyboardButton("🚀 AI Studio", callback_data="ai_studio")],
            [InlineKeyboardButton("❓ Yordam", callback_data="help")]
        ]
        await update.message.reply_text(
            f"{BOT_NAME}\n\n💎 <b>Xush kelibsiz!</b>\n\n"
            "• Instagram yuklash\n• AI rasm\n• Referat\n• Uzun matn\n• AI Studio — marketing paket",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.HTML
        )

    # ---------- AI STUDIO ----------
    async def ai_studio_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        context.user_data['ai_studio'] = {'premium': is_user_premium(user_id)}
        styles_kb = [[InlineKeyboardButton(f"🌟 {v}", callback_data=f"style_{k}")] for k, v in STYLES.items()]
        await query.message.edit_text(
            "<b>🎨 AI Studio</b>\n\nQisqa matn yozing, to'liq marketing paket oling.\n\n<i>Uslubni tanlang:</i>",
            reply_markup=InlineKeyboardMarkup(styles_kb),
            parse_mode=ParseMode.HTML
        )

    async def ai_studio_style_selected(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        style = query.data.replace("style_", "")
        context.user_data['ai_studio']['style'] = style
        await query.message.edit_text(
            f"✅ Uslub: <b>{STYLES[style]}</b>\n\nEndi qisqa matn yozing (masalan: 'Yangi coffee shop'):",
            parse_mode=ParseMode.HTML
        )
        context.user_data['step'] = 'waiting_for_ai_studio_input'

    async def process_ai_studio_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE, user_input: str):
        user_id = update.effective_user.id
        status = await update.message.reply_text("🚀 AI Studio ishga tushdi...")
        data = context.user_data.get('ai_studio', {})
        style = data.get('style', 'realistic')
        is_prem = data.get('premium', False)

        # 1. Prompt
        await status.edit_text("📝 Prompt yaratilmoqda...")
        base = generate_prompt_from_input(user_input)
        full_prompt = f"{base}, {STYLES[style]}"

        # 2. Rasm
        await status.edit_text("🎨 Rasm chizilmoqda...")
        img_bytes = generate_image_from_prompt(full_prompt)
        if not img_bytes:
            await status.edit_text("❌ Rasm yaratib bo'lmadi.")
            return

        # 3. Formatlar
        await status.edit_text("🖼 Formatlarga o'tkazilmoqda...")
        formats = {"feed": (1024,1024), "story": (1080,1920), "banner": (1920,1080)}
        media_group = []
        for name, size in formats.items():
            resized = resize_image_smart(img_bytes, size)
            if not is_prem:
                resized = add_watermark(resized)
            media_group.append(InputMediaPhoto(media=resized))
        if not is_prem:
            media_group = [media_group[0]]

        # 4. Matnlar
        await status.edit_text("✍️ Caption va hashtaglar...")
        cap_prompt = f"Business: {user_input}"
        cap_system = "3 xil caption yozing (business, emotional, short viral)."
        caption = generate_ai_text(cap_prompt, cap_system) or "Ajoyib post!"
        tag_prompt = f"Niche: {user_input}"
        tag_system = "10 ta eng yaxshi hashtag (ingliz va o'zbek)."
        hashtags = generate_ai_text(tag_prompt, tag_system) or "#ai #marketing"

        await status.delete()
        try:
            if len(media_group) > 1:
                await context.bot.send_media_group(chat_id=update.effective_chat.id, media=media_group)
            else:
                await context.bot.send_photo(chat_id=update.effective_chat.id, photo=media_group[0].media)
            final_text = (
                f"<b>📦 Marketing paket tayyor!</b>\n\n"
                f"<b>✍️ Caption:</b>\n{caption}\n\n"
                f"<b>#️⃣ Hashtaglar:</b>\n{hashtags}\n\n"
                f"{'⚠️ Premium emas — 1 format + watermark.' if not is_prem else '✅ Premium — to\'liq paket!'}\n\n{BOT_USERNAME}"
            )
            await update.message.reply_text(final_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Bosh menyu", callback_data="start_menu")]]), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"AI Studio send error: {e}")
            await update.message.reply_text("❌ Natijani yuborishda xatolik.")
        context.user_data['step'] = None
        context.user_data.pop('ai_studio', None)

    # ---------- REFERAT & UZUN MATN ----------
    async def generate_referat(self, update: Update, context: ContextTypes.DEFAULT_TYPE, topic: str):
        status = await update.message.reply_text("📑 Referat tuzilmoqda...")
        system = "Akademik referat rejasi (5-6 bo'lim, kirish, xulosa). O'zbek tilida."
        prompt = f"Mavzu: {topic}"
        result = generate_ai_text(prompt, system)
        if result:
            await status.edit_text(f"📑 <b>{topic}</b>\n\n{result}\n\n{BOT_USERNAME}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]))
        else:
            await status.edit_text("❌ Xatolik.")

    async def generate_long_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE, short_text: str):
        status = await update.message.reply_text("⚡️ Matn kengaytirilmoqda...")
        system = "Qisqa fikrni 200-300 so'zga kengaytir. O'zbek tilida."
        prompt = f"Qisqa matn: {short_text}"
        result = generate_ai_text(prompt, system)
        if result:
            await status.edit_text(f"📝 <b>Kengaytirilgan matn:</b>\n\n{result}\n\n{BOT_USERNAME}", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]))
        else:
            await status.edit_text("❌ Xatolik.")

    # ---------- RASM YARATISH (oddiy) ----------
    async def generate_image(self, update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str):
        status = await update.message.reply_text("🎨 Rasm yaratilmoqda...")
        try:
            url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
            resp = requests.get(url, timeout=90)
            if resp.status_code == 200:
                path = f"{DOWNLOAD_DIR}/gen_{uuid.uuid4()}.jpg"
                with open(path, "wb") as f: f.write(resp.content)
                caption = f"✨ <b>AI yaratdi!</b>\n📝 {prompt}\n\n{BOT_USERNAME}"
                with open(path, 'rb') as ph:
                    await context.bot.send_photo(chat_id=update.effective_chat.id, photo=ph, caption=caption, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Yana", callback_data="new_image")], [InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]))
                await status.delete()
                os.remove(path)
            else:
                await status.edit_text("❌ Rasm yaratib bo'lmadi.")
        except Exception as e:
            logger.error(f"Image error: {e}")
            await status.edit_text("❌ Xatolik.")

    # ---------- INSTAGRAM YUKLASH ----------
    async def download_instagram(self, update: Update, context: ContextTypes.DEFAULT_TYPE, url: str):
        user_id = update.effective_user.id
        if not await check_subscription(update, context):
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
        await update.message.reply_text("🕹 <b>Admin paneli</b>", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)

    async def broadcast_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID: return
        if not update.message.reply_to_message:
            await update.message.reply_text("Reply qiling.")
            return
        status = await update.message.reply_text("📢 Yuborilmoqda...")
        conn = get_db_connection()
        users = conn.execute("SELECT user_id FROM users").fetchall()
        conn.close()
        sent = failed = 0
        for u in users:
            try:
                await context.bot.copy_message(u['user_id'], update.effective_chat.id, update.message.reply_to_message.message_id)
                sent += 1
                await asyncio.sleep(0.05)
            except: failed += 1
        await status.edit_text(f"✅ Yetkazildi: {sent}\n❌ Bloklagan: {failed}")

    # ---------- CALLBACK HANDLER (to'liq) ----------
    async def callback_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        user_id = query.from_user.id
        try: await query.answer()
        except: pass

        # --- umumiy tugmalar ---
        if query.data == "check_sub":
            if await check_subscription(update, context):
                try: await query.message.delete()
                except: pass
                await self.start(update, context)
            else:
                await query.answer("❌ Obuna bo'lmagansiz!", show_alert=True)
            return
        if query.data == "start_menu":
            try: await query.message.delete()
            except: pass
            await self.start(update, context)
            return
        if query.data == "instagram_info":
            context.user_data['step'] = 'waiting_for_instagram'
            await query.message.edit_text("📥 Instagram linkini yuboring:")
            return
        if query.data == "generate_image":
            context.user_data['step'] = 'waiting_for_prompt'
            await query.message.edit_text("🖼 Rasm uchun tavsif yozing:")
            return
        if query.data == "new_image":
            context.user_data['step'] = 'waiting_for_prompt'
            try: await query.message.delete()
            except: pass
            await context.bot.send_message(user_id, "🖼 Yangi rasm uchun tavsif yozing:")
            return
        if query.data == "referat":
            context.user_data['step'] = 'waiting_for_referat_topic'
            await query.message.edit_text("📑 Referat mavzusini yozing:")
            return
        if query.data == "long_text":
            context.user_data['step'] = 'waiting_for_long_text'
            await query.message.edit_text("⚡️ Qisqa matn yozing, kengaytiraman:")
            return
        if query.data == "ai_studio":
            await self.ai_studio_start(update, context)
            return
        if query.data.startswith("style_"):
            await self.ai_studio_style_selected(update, context)
            return
        if query.data == "help":
            help_text = f"📚 {BOT_NAME}\n\nInstagram yuklash, AI rasm, Referat, Uzun matn, AI Studio."
            await query.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menyu", callback_data="start_menu")]]), parse_mode=ParseMode.HTML)
            return

        # --- admin panel ---
        if user_id != ADMIN_ID: return
        try:
            if query.data == "admin_stat":
                conn = get_db_connection()
                count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                conn.close()
                await query.message.edit_text(f"📊 Foydalanuvchilar: {count}", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            elif query.data == "toggle_sub":
                conn = get_db_connection()
                conn.execute("UPDATE settings SET force_sub = 1 - force_sub WHERE id = 1")
                conn.commit()
                conn.close()
                await query.message.edit_text("🕹 Admin paneli", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
                await query.answer("✅ O'zgartirildi!")
            elif query.data == "add_channel":
                context.user_data['step'] = 'add_force_channel'
                await query.message.edit_text("➕ Kanal username yoki havolasini yuboring:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel")]]))
            elif query.data == "remove_channel":
                context.user_data['step'] = 'remove_force_channel'
                channels = get_force_channels()
                txt = "➖ O'chirish uchun kanalni yuboring:\n\n" + "\n".join(channels) if channels else "Ro'yxat bo'sh."
                await query.message.edit_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor", callback_data="cancel")]]))
            elif query.data == "list_channels":
                channels = get_force_channels()
                txt = "📋 Kanallar:\n" + "\n".join(channels) if channels else "Ro'yxat bo'sh."
                await query.message.edit_text(txt, reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            elif query.data == "send_help":
                await query.message.edit_text("📢 Reklama: xabarga reply qilib /send", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Orqaga", callback_data="admin_panel")]]))
            elif query.data == "admin_panel":
                await query.message.edit_text("🕹 Admin paneli", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
            elif query.data == "cancel":
                context.user_data['step'] = None
                await query.message.edit_text("🕹 Admin paneli", reply_markup=admin_keyboard(), parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"Admin callback error: {e}")

    # ---------- MATN XABARLARNI QAYTA ISHLASH ----------
    async def handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        text = update.message.text.strip()
        step = context.user_data.get('step')

        # Admin kanal qo'shish/o'chirish
        if user_id == ADMIN_ID:
            if step == 'add_force_channel':
                if add_force_channel(text):
                    await update.message.reply_text(f"✅ Qo'shildi: {extract_channel_id(text)}", reply_markup=admin_keyboard())
                else:
                    await update.message.reply_text("⚠️ Mavjud.", reply_markup=admin_keyboard())
                context.user_data['step'] = None
                return
            if step == 'remove_force_channel':
                remove_force_channel(text)
                await update.message.reply_text(f"✅ O'chirildi: {extract_channel_id(text)}", reply_markup=admin_keyboard())
                context.user_data['step'] = None
                return

        # AI Studio
        if step == 'waiting_for_ai_studio_input':
            context.user_data['step'] = None
            await self.process_ai_studio_input(update, context, text)
            return
        # Referat
        if step == 'waiting_for_referat_topic':
            context.user_data['step'] = None
            await self.generate_referat(update, context, text)
            return
        # Uzun matn
        if step == 'waiting_for_long_text':
            context.user_data['step'] = None
            await self.generate_long_text(update, context, text)
            return
        # Rasm
        if step == 'waiting_for_prompt':
            context.user_data['step'] = None
            await self.generate_image(update, context, text)
            return
        # Instagram kutish
        if step == 'waiting_for_instagram':
            context.user_data['step'] = None
            if "instagram.com" in text.lower():
                await self.download_instagram(update, context, text)
            else:
                await update.message.reply_text("❌ Instagram linki emas!")
            return

        # To'g'ridan-to'g'ri Instagram linki
        if "instagram.com" in text.lower():
            await self.download_instagram(update, context, text)
            return

        await update.message.reply_text("Iltimos, menyudan tanlang yoki link yuboring.")

# ====================== ASOSIY DASTUR ======================
if __name__ == "__main__":
    bot = InstagramDownloader()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", bot.start))
    app.add_handler(CommandHandler("admin", bot.admin_panel))
    app.add_handler(CommandHandler("send", bot.broadcast_send))
    app.add_handler(CallbackQueryHandler(bot.callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.handle_text))
    print(f"🚀 {BOT_NAME} to'liq ishga tushdi!")
    app.run_polling(drop_pending_updates=True)
