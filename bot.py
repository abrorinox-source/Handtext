import os
from copy import deepcopy
import time
import threading
import asyncio
import re
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import requests
import base64
import logging
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http import HTTPStatus
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
import uvicorn

try:
    from google.cloud import firestore
except Exception:  # pragma: no cover - runtime optional dependency
    firestore = None

# .env faylini yuklash
load_dotenv()

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

_firestore_client = None
_firestore_init_attempted = False

PREVIEW_CACHE_TTL_SECONDS = 180
AVAILABLE_FONT_SIZES = (56, 64, 72, 80, 88, 96, 104, 112)
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_GEMINI_CONNECT_TIMEOUT_SECONDS = 20
DEFAULT_GEMINI_READ_TIMEOUT_SECONDS = 180
DEFAULT_GEMINI_MAX_RETRIES = 1

# Default settings
DEFAULT_SETTINGS = {
    'font_id': 1,
    'font_size': 56,
    'font_margin_target_font_id': 1,
    'font_top_margin_overrides': {},
    'preview_template_offset_y': 0,
    'preview_layout': 'school_graph',
    'text_color': [0, 0, 255],
    'output_format': 'png',
    'page_size': 'a4',
    'dpi': 300,
    'text_alignment': 'left',
    'margin_left': 240,
    'margin_right': 240,
    'margin_top': 240,
    'margin_bottom': 240,
    'line_height_multiplier': 2.0,
    'line_spacing_adjust_px': 0.0,
    'line_break_spacing': 80,
    'enable_word_rotation': False,
    'word_rotation_range': 5.0,
    'enable_natural_variation': False,
    'natural_variation_alpha': 15,
    'natural_variation_sigma': 5,
    'enable_ink_flow': False,
    'ink_flow_intensity': 1.0
}

CYRILLIC_SUPPORTED_FONT_IDS = {
    1, 10, 11, 18, 19, 21, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 83, 84, 85, 87, 88, 89, 90
}
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")

PAGE_SIZE_NAMES = {
    'a4': 'A4',
    'a5': 'A5',
    'letter': 'Letter',
    'postcard_us': 'Postcard US',
    'card_5x7': 'Card 5x7',
    'card_a2': 'Card A2'
}

COLOR_NAMES = {
    '[0, 0, 255]': 'Blue',
    '[0, 0, 0]': 'Black',
    '[255, 0, 0]': 'Red',
    '[0, 128, 0]': 'Green',
    '[0, 0, 139]': 'Dark blue',
    '[128, 0, 128]': 'Purple'
}

PREVIEW_LAYOUT_NAMES = {
    'plain': 'A4',
    'school_graph': 'Math Notebook',
    'school_graph_right': 'Math Notebook Right',
    'school_graph_nored': 'Math Notebook No Red',
    'ona_tili': 'Language Notebook',
    'ona_tili_right': 'Language Notebook Right',
    'ona_tili_nored': 'Language Notebook No Red'
}

PREVIEW_PAGE_WIDTH = 1200
PAGE_ASPECT_RATIOS = {
    'a4': 210 / 297,
    'a5': 148 / 210,
    'letter': 8.5 / 11
}

ONA_TILI_TEMPLATE_BASE_OFFSET_Y = -15
ONA_TILI_RED_MARGIN_OFFSET_X = -20
ONA_TILI_TEXT_MARGIN_OFFSET_X = 25
ONA_TILI_RIGHT_MARGIN_OFFSET_X = 40

SCHOOL_GRAPH_FONT_LINE_MAP = {
    56: 63,
    64: 63,
    72: 62.5,
    80: 62,
    88: 62.6,
    96: 62.86,
    104: 62.9,
    112: 64.5
}

ONA_TILI_FONT_LINE_MAP = {
    56: 73,
    64: 73,
    72: 72.5,
    80: 73,
    88: 72.6,
    96: 72.86,
    104: 72.9,
    112: 73
}

ONA_TILI_TOP_MARGIN_BY_SIZE = {
    80: -10,
    88: -10,
    96: -15,
    104: -20,
    112: -30
}

SCHOOL_GRAPH_TOP_MARGIN_BY_SIZE = {
    72: -10,
    80: -10,
    88: -20,
    96: -25,
    104: -25,
    112: -35
}

SCHOOL_GRAPH_RIGHT_PARAGRAPH_SPACING_BY_SIZE = {
    56: 62,
    64: 62,
    72: 61,
    80: 61,
    88: 62,
    96: 62,
    104: 62,
    112: 64
}

ONA_TILI_RIGHT_PARAGRAPH_SPACING_BY_SIZE = {
    56: 72,
    64: 72,
    72: 71,
    80: 72,
    88: 71,
    96: 71,
    104: 71,
    112: 72
}

SCHOOL_GRAPH_TOP_MARGIN_BY_FONT = {
    2: -15,
    3: -14,
    5: -19
}

def get_school_graph_font_top_margin_offset(font_id):
    """School graph preview uchun fontga mos top margin offsetini qaytaradi"""
    if font_id in SCHOOL_GRAPH_TOP_MARGIN_BY_FONT:
        return SCHOOL_GRAPH_TOP_MARGIN_BY_FONT[font_id]
    if font_id == 1:
        return 0
    return -14

def get_user_settings(context, user_id):
    """Foydalanuvchi sozlamalarini olish"""
    if not context.bot_data.get('user_settings'):
        context.bot_data['user_settings'] = {}
    
    if user_id not in context.bot_data['user_settings']:
        context.bot_data['user_settings'][user_id] = deepcopy(DEFAULT_SETTINGS)
    
    return context.bot_data['user_settings'][user_id]

def update_user_setting(context, user_id, key, value):
    """Foydalanuvchi sozlamasini yangilash"""
    settings = get_user_settings(context, user_id)
    settings[key] = value

def get_admin_user_ids():
    """Admin user ID lar ro'yxatini qaytaradi"""
    raw_value = os.getenv('ADMIN_USER_IDS', '').strip()
    if not raw_value:
        return set()
    admin_ids = set()
    for part in raw_value.split(','):
        part = part.strip()
        if not part:
            continue
        try:
            admin_ids.add(int(part))
        except ValueError:
            logger.warning(f"Invalid value found in ADMIN_USER_IDS: {part}")
    return admin_ids

def is_admin(user_id):
    """Foydalanuvchi admin ekanini tekshiradi"""
    return user_id in get_admin_user_ids()

def _get_firestore_client():
    """Firestore clientni lazy init qiladi. Yo'q bo'lsa None qaytaradi."""
    global _firestore_client, _firestore_init_attempted

    if _firestore_client is not None:
        return _firestore_client
    if _firestore_init_attempted:
        return None

    _firestore_init_attempted = True
    if firestore is None:
        logger.warning("google-cloud-firestore package is not installed; Firestore disabled")
        return None

    if os.getenv("FIRESTORE_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
        logger.info("FIRESTORE_ENABLED=false, Firestore disabled")
        return None

    try:
        project_id = os.getenv("FIRESTORE_PROJECT_ID")
        _firestore_client = firestore.Client(project=project_id) if project_id else firestore.Client()
        logger.info("Firestore client initialized")
        return _firestore_client
    except Exception as exc:
        logger.error(f"Failed to initialize Firestore client: {exc}")
        return None

def sync_user_profile_to_firestore(user):
    """User profilini Firestore users kolleksiyasiga yozadi."""
    db = _get_firestore_client()
    if db is None or user is None:
        return
    try:
        user_ref = db.collection("users").document(str(user.id))
        user_ref.set(
            {
                "user_id": user.id,
                "username": user.username,
                "full_name": user.full_name,
                "last_seen_at": firestore.SERVER_TIMESTAMP,
                "last_seen_date": time.strftime("%Y-%m-%d"),
            },
            merge=True,
        )
    except Exception as exc:
        logger.error(f"Failed to sync user profile to Firestore: {exc}")

def get_user_balance_credits(user_id):
    """User balansini Firestore'dan o'qiydi. Topilmasa 0."""
    db = _get_firestore_client()
    if db is None:
        return 0
    try:
        doc = db.collection("users").document(str(user_id)).get()
        if not doc.exists:
            return 0
        data = doc.to_dict() or {}
        return int(data.get("balance_credits", 0))
    except Exception as exc:
        logger.error(f"Failed to read user balance from Firestore: {exc}")
        return 0

def add_user_credits(user_id, delta, actor_user_id=None, reason="manual"):
    """User balansiga credit qo'shadi/yeydi va payments log yozadi."""
    db = _get_firestore_client()
    if db is None:
        raise RuntimeError("Firestore is not available")
    if delta == 0:
        return get_user_balance_credits(user_id)

    user_ref = db.collection("users").document(str(user_id))
    payment_ref = db.collection("payments").document()

    try:
        user_ref.set({"user_id": int(user_id)}, merge=True)
        user_ref.update(
            {
                "balance_credits": firestore.Increment(int(delta)),
                "last_balance_update_at": firestore.SERVER_TIMESTAMP,
            }
        )
        payment_ref.set(
            {
                "payment_id": payment_ref.id,
                "user_id": int(user_id),
                "credits_delta": int(delta),
                "reason": reason,
                "status": "applied",
                "actor_user_id": int(actor_user_id) if actor_user_id else None,
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )
    except Exception as exc:
        logger.error(f"Failed to update credits in Firestore: {exc}")
        raise

    return get_user_balance_credits(user_id)

def log_usage_event(user_id, action, payload=None):
    """Usage eventni Firestore usage kolleksiyasiga yozadi."""
    db = _get_firestore_client()
    if db is None:
        return
    try:
        usage_ref = db.collection("usage").document()
        usage_ref.set(
            {
                "usage_id": usage_ref.id,
                "user_id": int(user_id),
                "action": action,
                "payload": payload or {},
                "created_at": firestore.SERVER_TIMESTAMP,
            }
        )
    except Exception as exc:
        logger.error(f"Failed to log usage event: {exc}")

def build_main_menu_keyboard():
    """Asosiy foydalanuvchi keyboardi."""
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("Balance"), KeyboardButton("Buy credits")],
            [KeyboardButton("Settings")],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )

def build_buy_packages_keyboard():
    """Credit paketlari uchun inline keyboard."""
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("50 credits - 15,000 UZS", callback_data="buypkg:50:15000")],
            [InlineKeyboardButton("100 credits - 25,000 UZS", callback_data="buypkg:100:25000")],
            [InlineKeyboardButton("250 credits - 55,000 UZS", callback_data="buypkg:250:55000")],
        ]
    )

def contains_cyrillic_text(text):
    """Matnda kirill harflari bor-yo'qligini tekshiradi."""
    return bool(CYRILLIC_RE.search(text or ""))

def get_gemini_image_api_key():
    """Gemini image API key ni qaytaradi."""
    return (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GEMINI_KEY")
    )

def get_gemini_image_model():
    """Gemini image model nomini qaytaradi."""
    return os.getenv("GEMINI_IMAGE_MODEL", DEFAULT_GEMINI_IMAGE_MODEL).strip() or DEFAULT_GEMINI_IMAGE_MODEL

def get_gemini_image_timeouts():
    """Gemini API timeoutlarini qaytaradi."""
    connect_timeout = int(os.getenv("GEMINI_CONNECT_TIMEOUT", str(DEFAULT_GEMINI_CONNECT_TIMEOUT_SECONDS)))
    read_timeout = int(os.getenv("GEMINI_READ_TIMEOUT", str(DEFAULT_GEMINI_READ_TIMEOUT_SECONDS)))
    return max(5, connect_timeout), max(30, read_timeout)

def get_gemini_image_max_retries():
    """Gemini API maksimal retry sonini qaytaradi."""
    return 1

def extract_gemini_image_bytes_from_response(response_json):
    """Gemini response ichidan birinchi image bytes ni ajratadi."""
    candidates = response_json.get("candidates", [])
    for candidate in candidates:
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        for part in parts:
            inline_data = part.get("inlineData") or part.get("inline_data")
            if not inline_data:
                continue
            data_b64 = inline_data.get("data")
            if not data_b64:
                continue
            mime_type = inline_data.get("mimeType") or inline_data.get("mime_type") or "image/png"
            try:
                return base64.b64decode(data_b64), mime_type
            except Exception:
                continue
    return None, None

def build_ai_editor_help_text():
    return (
        "AI editor ishlatish:\n\n"
        "1. Avval rasm yuboring (photo yoki image file)\n"
        "2. Keyin /aiedit <prompt> yuboring\n\n"
        "Misol:\n"
        "/aiedit Make this look like a realistic phone photo on a wooden table.\n\n"
        "Yoki rasmga reply qilib ham yuborishingiz mumkin:\n"
        "/aiedit Add paper texture and soft natural shadows."
    )

def get_default_ai_realistic_prompt(layout="plain"):
    """Layoutga mos default realistic edit prompt."""
    common_rules = (
        "Render as a realistic smartphone photo taken from above with the page placed on a desk/table surface. "
        "Ensure the desk surface is visible around the paper by about 1 cm on all four sides. "
        "Focus on realistic paper relief, paper texture, soft natural shadows, and believable desk material details. "
        "Do not add logos, watermarks, or extra objects."
    )

    if layout == "plain":
        return (
            "Convert this into a realistic photo of a clean A4 white paper page. "
            "Use plain white paper only (no notebook lines, no grid, no red margins). "
            f"{common_rules}"
        )

    if layout in ("school_graph", "school_graph_right", "school_graph_nored"):
        redline_rule = "Keep the single red vertical margin line on the left side." if layout == "school_graph" else (
            "Keep the single red vertical margin line on the right side." if layout == "school_graph_right" else
            "Do not include any red vertical margin line."
        )
        return (
            "Convert this into a realistic math notebook page photo. "
            "Keep the square blue grid notebook pattern visible and natural. "
            f"{redline_rule} "
            f"{common_rules}"
        )

    if layout in ("ona_tili", "ona_tili_right", "ona_tili_nored"):
        redline_rule = "Keep the red vertical margin line on the left side." if layout == "ona_tili" else (
            "Keep the red vertical margin line on the right side." if layout == "ona_tili_right" else
            "Do not include any red vertical margin line."
        )
        return (
            "Convert this into a realistic language notebook page photo. "
            "Keep the language notebook ruling pattern and spacing natural. "
            f"{redline_rule} "
            f"{common_rules}"
        )

    return (
        "Convert this into a realistic smartphone photo of a real handwritten page. "
        f"{common_rules}"
    )

async def extract_image_reference_from_message(message):
    """Telegram message ichidan image reference qaytaradi."""
    if message.photo:
        largest_photo = message.photo[-1]
        return {
            "file_id": largest_photo.file_id,
            "mime_type": "image/jpeg",
            "source_type": "photo",
        }

    if message.document and (message.document.mime_type or "").startswith("image/"):
        return {
            "file_id": message.document.file_id,
            "mime_type": message.document.mime_type or "image/png",
            "source_type": "document",
        }

    return None

async def download_telegram_file_bytes(context, file_id):
    """Telegram file ni bytes ko'rinishida yuklaydi."""
    tg_file = await context.bot.get_file(file_id)
    data = await tg_file.download_as_bytearray()
    return bytes(data)

def call_gemini_image_edit(prompt, image_bytes, mime_type, api_key, model):
    """Gemini image edit so'rovini yuboradi va image bytes qaytaradi."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": base64.b64encode(image_bytes).decode("utf-8"),
                        }
                    },
                ]
            }
        ],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"]
        },
    }

    connect_timeout, read_timeout = get_gemini_image_timeouts()
    max_retries = get_gemini_image_max_retries()
    last_error = None
    start_ts = time.time()

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=(connect_timeout, read_timeout))
        except requests.exceptions.Timeout:
            last_error = Exception("AI editor so'rovi timeout bo'ldi.")
            logger.warning(f"Gemini timeout on attempt {attempt}/{max_retries}")
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
                continue
            raise last_error
        except requests.exceptions.RequestException as exc:
            last_error = Exception(f"AI editor network xatoligi: {exc}")
            logger.warning(f"Gemini network error on attempt {attempt}/{max_retries}: {exc}")
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
                continue
            raise last_error

        if response.status_code in (429, 500, 502, 503, 504):
            try:
                err = response.json()
                message = err.get("error", {}).get("message") or response.text
            except Exception:
                message = response.text
            last_error = Exception(f"Gemini API vaqtinchalik xatoligi ({response.status_code}): {message}")
            logger.warning(f"Gemini transient error on attempt {attempt}/{max_retries}: {response.status_code}")
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
                continue
            raise last_error

        if response.status_code != 200:
            try:
                err = response.json()
                message = err.get("error", {}).get("message") or response.text
            except Exception:
                message = response.text
            raise Exception(f"Gemini API xatoligi ({response.status_code}): {message}")

        response_json = response.json()
        result_bytes, result_mime = extract_gemini_image_bytes_from_response(response_json)
        if not result_bytes:
            raise Exception("Gemini javobida image topilmadi.")
        elapsed = time.time() - start_ts
        logger.info(f"Gemini image edit completed in {elapsed:.2f}s (attempt {attempt}/{max_retries})")
        return result_bytes, result_mime or "image/png"

    raise last_error or Exception("AI editor noma'lum xatolik bilan to'xtadi.")

def get_telegram_photo_bytes_for_ai_result(result_bytes, result_mime):
    """AI natijani Telegram photo uchun optimallashtiradi."""
    mime = (result_mime or "").lower()
    if "png" in mime:
        try:
            return convert_png_bytes_to_jpg_bytes(result_bytes)
        except Exception:
            return result_bytes
    return result_bytes

def get_cyrillic_supported_fonts_text():
    """Kirillni support qiladigan shriftlar ro'yxatini matn ko'rinishida qaytaradi."""
    return ", ".join(str(font_id) for font_id in sorted(CYRILLIC_SUPPORTED_FONT_IDS))

def build_cyrillic_confirmation_keyboard(confirm_key):
    """Kirill support ogohlantirishi uchun tasdiqlash tugmalari."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Ha, davom etish", callback_data=f"cyrconfirm:yes:{confirm_key}"),
            InlineKeyboardButton("Yo'q, bekor qilish", callback_data=f"cyrconfirm:no:{confirm_key}")
        ]
    ])

async def start_preview_flow(
    message,
    context,
    user_text,
    settings,
    api_key,
    text_key,
    user_id,
    reply_to_message_id=None
):
    """Notebook/oddiy preview oqimini bitta joyda bajaradi."""
    layout = settings.get('preview_layout', 'plain')

    if is_notebook_preview_layout(layout):
        pending_key = f"{user_id}_{text_key}_{int(time.time())}"
        if not context.bot_data.get('pending_preview_choices'):
            context.bot_data['pending_preview_choices'] = {}
        context.bot_data['pending_preview_choices'][pending_key] = {
            'user_text': user_text,
            'settings': deepcopy(settings),
            'user_id': user_id,
            'text_key': text_key,
            'created_at': time.time()
        }
        await message.reply_text(
            "Choose notebook side before previewing:",
            reply_markup=build_notebook_side_choice_keyboard(pending_key)
        )
        return

    await send_generated_preview_message(
        message,
        context,
        user_text,
        settings,
        api_key,
        text_key,
        user_id,
        reply_to_message_id=reply_to_message_id
    )

def get_bot_commands():
    """Telegram slash commands for left-side command menu."""
    return [
        BotCommand("start", "Start bot and open menu"),
        BotCommand("set", "Open settings"),
        BotCommand("font", "Set font by id"),
        BotCommand("size", "Set font size"),
        BotCommand("fonts", "Show font samples"),
        BotCommand("color", "Choose text color"),
        BotCommand("aiedit", "Edit last/replied image with AI"),
        BotCommand("balance", "Show credit balance"),
        BotCommand("buy", "Open credit packages"),
    ]

async def ensure_bot_commands(application):
    """Set Telegram slash commands at startup."""
    try:
        commands = get_bot_commands()
        # Set for broad scopes and language fallbacks so clients can pick them reliably.
        await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await application.bot.set_my_commands(commands, scope=BotCommandScopeDefault(), language_code="")
        await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats(), language_code="")
        logger.info("Bot commands menu configured")
    except Exception as exc:
        logger.error(f"Failed to set bot commands: {exc}")

async def sync_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force-sync bot commands and report result."""
    user = update.message.from_user
    register_user_activity(context, user)
    try:
        commands = get_bot_commands()
        await context.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await context.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await context.bot.set_my_commands(commands, scope=BotCommandScopeDefault(), language_code="")
        await context.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats(), language_code="")
        await update.message.reply_text("Commands synced. Reopen chat and check '/' menu.")
    except Exception as exc:
        await update.message.reply_text(f"Command sync failed: {exc}")

def register_user_activity(context, user):
    """User activity statistikasi uchun foydalanuvchini qayd qiladi"""
    if user is None:
        return

    if not context.bot_data.get('known_users'):
        context.bot_data['known_users'] = {}
    if not context.bot_data.get('activity_dates'):
        context.bot_data['activity_dates'] = {}

    user_id = user.id
    today = time.strftime("%Y-%m-%d")
    context.bot_data['known_users'][user_id] = {
        'username': user.username,
        'full_name': user.full_name,
        'last_seen_date': today
    }
    context.bot_data['activity_dates'][user_id] = today
    sync_user_profile_to_firestore(user)

def increment_stat(context, key):
    """Oddiy hisoblagichni oshiradi"""
    if not context.bot_data.get('stats'):
        context.bot_data['stats'] = {}
    context.bot_data['stats'][key] = context.bot_data['stats'].get(key, 0) + 1

def get_user_font_top_margin_override(settings, font_id):
    """User tomonidan berilgan font top margin override qiymatini qaytaradi"""
    overrides = settings.get('font_top_margin_overrides', {})
    return int(overrides.get(str(font_id), 0))

def set_user_font_top_margin_override(settings, font_id, value):
    """User uchun font top margin override qiymatini saqlaydi"""
    overrides = settings.setdefault('font_top_margin_overrides', {})
    if value == 0:
        overrides.pop(str(font_id), None)
    else:
        overrides[str(font_id)] = int(value)

def get_effective_font_top_margin_offset(settings, font_id, font_size):
    """School graph preview uchun size'dan mustaqil font top margin offset"""
    font_top_margin_offset = get_school_graph_font_top_margin_offset(font_id)
    user_font_top_margin_offset = get_user_font_top_margin_override(settings, font_id)
    return font_top_margin_offset + user_font_top_margin_offset

def has_paragraph_breaks(text):
    """Matnda paragraph break bor-yo'qligini tekshiradi"""
    return "\n\n" in text.replace("\r\n", "\n")

def get_effective_line_spacing_px(settings):
    """Layout va font size uchun mos line spacing px qiymatini qaytaradi"""
    layout = settings.get('preview_layout', 'plain')
    font_size = settings.get('font_size', 56)
    line_spacing_adjust_px = float(settings.get('line_spacing_adjust_px', 0.0))
    base_layout = get_notebook_layout_base(layout)
    if base_layout == 'ona_tili':
        return ONA_TILI_FONT_LINE_MAP.get(font_size, 73) + line_spacing_adjust_px
    if base_layout == 'school_graph':
        return SCHOOL_GRAPH_FONT_LINE_MAP.get(font_size, 63) + line_spacing_adjust_px
    return None

def get_effective_paragraph_spacing(settings):
    """Paragraph spacing qiymatini qaytaradi"""
    layout = settings.get('preview_layout', 'plain')
    configured_spacing = settings.get('line_break_spacing', 80)

    if is_notebook_preview_layout(layout):
        font_size = settings.get('font_size', 56)
        base_layout = get_notebook_layout_base(layout)
        if layout in ('school_graph_right', 'ona_tili_right'):
            if base_layout == 'ona_tili':
                return ONA_TILI_RIGHT_PARAGRAPH_SPACING_BY_SIZE.get(
                    font_size,
                    max(1, int(round(get_effective_line_spacing_px(settings) or configured_spacing)) - 1)
                )
            return SCHOOL_GRAPH_RIGHT_PARAGRAPH_SPACING_BY_SIZE.get(
                font_size,
                max(1, int(round(get_effective_line_spacing_px(settings) or configured_spacing)) - 1)
            )

        line_spacing_px = get_effective_line_spacing_px(settings)
        if line_spacing_px is not None:
            return int(round(line_spacing_px))

    return configured_spacing

def get_color_name(color):
    """Rang nomini qaytaradi"""
    return COLOR_NAMES.get(str(color), f'RGB{color}')

def get_preview_layout_name(layout):
    """Preview layout nomini qaytaradi"""
    return PREVIEW_LAYOUT_NAMES.get(layout, layout)

def get_notebook_layout_base(layout):
    """Notebook layoutning asosiy turini qaytaradi."""
    if layout in ('school_graph_right', 'school_graph', 'school_graph_nored'):
        return 'school_graph'
    if layout in ('ona_tili_right', 'ona_tili', 'ona_tili_nored'):
        return 'ona_tili'
    return layout

def is_right_notebook_layout(layout):
    """Notebook layoutning o'ng sahifa varianti ekanini tekshiradi."""
    return layout in ('school_graph_right', 'ona_tili_right')

def build_page_size_keyboard(prefix, include_back=True):
    """Page size tanlash keyboardini quradi"""
    rows = [
        [
            InlineKeyboardButton("A4", callback_data=f"{prefix}:a4"),
            InlineKeyboardButton("A5", callback_data=f"{prefix}:a5")
        ],
        [
            InlineKeyboardButton("Letter", callback_data=f"{prefix}:letter"),
            InlineKeyboardButton("Postcard", callback_data=f"{prefix}:postcard_us")
        ],
        [
            InlineKeyboardButton("Card 5x7", callback_data=f"{prefix}:card_5x7"),
            InlineKeyboardButton("Card A2", callback_data=f"{prefix}:card_a2")
        ]
    ]
    if include_back:
        rows.append([InlineKeyboardButton("Back", callback_data="setting:previewlayout")])
    return InlineKeyboardMarkup(rows)

def is_notebook_preview_layout(layout):
    """Daftar tipidagi preview layoutlarni tekshiradi"""
    return layout in (
        'school_graph', 'school_graph_right', 'school_graph_nored',
        'ona_tili', 'ona_tili_right', 'ona_tili_nored'
    )

def get_school_graph_metrics(page_size='a5'):
    """School graph preview uchun katak va margin o'lchamlarini hisoblaydi"""
    preview_width = PREVIEW_PAGE_WIDTH
    aspect_ratio = PAGE_ASPECT_RATIOS.get(page_size, PAGE_ASPECT_RATIOS['a5'])
    preview_height = round(preview_width / aspect_ratio)
    base_step = max(18, round(preview_width / 28))
    red_margin = base_step * 3
    text_margin = red_margin + (base_step * 2)
    top_margin = base_step * 2

    return {
        'preview_width': preview_width,
        'preview_height': preview_height,
        'base_step': base_step,
        'red_margin': red_margin,
        'text_margin': text_margin,
        'top_margin': top_margin
    }

def get_effective_preview_settings(settings):
    """Preview uchun layoutga mos sozlamalarni qaytaradi"""
    preview_settings = settings.copy()
    layout = settings.get('preview_layout', 'plain')

    if is_notebook_preview_layout(layout):
        metrics = get_school_graph_metrics('a5')
        font_id = settings.get('font_id', 1)
        font_size = settings.get('font_size', 48)
        global_top_margin_offset = settings.get('margin_top', DEFAULT_SETTINGS.get('margin_top', 240)) - DEFAULT_SETTINGS.get('margin_top', 240)
        base_layout = get_notebook_layout_base(layout)
        if base_layout == 'ona_tili':
            line_spacing_px = ONA_TILI_FONT_LINE_MAP.get(font_size, 63)
            size_top_margin_offset = ONA_TILI_TOP_MARGIN_BY_SIZE.get(font_size, 0)
        else:
            line_spacing_px = SCHOOL_GRAPH_FONT_LINE_MAP.get(font_size, 63)
            size_top_margin_offset = SCHOOL_GRAPH_TOP_MARGIN_BY_SIZE.get(font_size, 0)
        top_margin_offset = get_effective_font_top_margin_offset(settings, font_id, font_size)
        top_margin = metrics['top_margin'] + global_top_margin_offset + size_top_margin_offset + top_margin_offset
        preview_settings['page_size'] = 'a5'
        preview_settings['font_size'] = font_size
        if layout == 'ona_tili':
            preview_settings['margin_left'] = metrics['text_margin'] + ONA_TILI_TEXT_MARGIN_OFFSET_X
            preview_settings['margin_right'] = metrics['base_step'] + ONA_TILI_RIGHT_MARGIN_OFFSET_X
        elif layout == 'ona_tili_right':
            preview_settings['margin_left'] = metrics['base_step'] + ONA_TILI_RIGHT_MARGIN_OFFSET_X
            preview_settings['margin_right'] = metrics['text_margin'] + ONA_TILI_TEXT_MARGIN_OFFSET_X
        elif layout == 'ona_tili_nored':
            preview_settings['margin_left'] = metrics['red_margin'] + ONA_TILI_TEXT_MARGIN_OFFSET_X
            preview_settings['margin_right'] = metrics['base_step'] + ONA_TILI_RIGHT_MARGIN_OFFSET_X
        elif layout == 'school_graph_right':
            preview_settings['margin_left'] = metrics['base_step']
            preview_settings['margin_right'] = metrics['text_margin']
        elif layout == 'school_graph_nored':
            preview_settings['margin_left'] = metrics['red_margin']
            preview_settings['margin_right'] = metrics['base_step']
        else:
            preview_settings['margin_left'] = metrics['text_margin']
            preview_settings['margin_right'] = metrics['base_step']
        preview_settings['margin_top'] = top_margin
        preview_settings['margin_bottom'] = max(12, round(metrics['base_step'] * 0.5))
        preview_settings['line_height_multiplier'] = round(line_spacing_px / font_size, 2)
    else:
        preview_settings['page_size'] = 'a4'

    return preview_settings

def build_school_graph_background(size, offset_y=0):
    """Katakli maktab daftariga o'xshash fon yaratadi"""
    return build_school_graph_background_side(size, offset_y, side='left')

def build_school_graph_background_side(size, offset_y=0, side='left'):
    """Katakli maktab daftariga o'xshash fon yaratadi."""
    width, height = size
    image = Image.new("RGBA", size, (251, 252, 248, 255))
    draw = ImageDraw.Draw(image)

    base_step = max(18, round(width / 28))
    red_margin = base_step * 3

    grid_color = (133, 201, 232, 150)
    margin_color = (220, 60, 60, 220)

    for x in range(0, width, base_step):
        draw.line((x, 0, x, height), fill=grid_color, width=1)

    start_y = offset_y
    while start_y > 0:
        start_y -= base_step

    for y in range(start_y, height, base_step):
        draw.line((0, y, width, y), fill=grid_color, width=1)

    if side != 'none':
        red_x = red_margin if side == 'left' else max(0, width - red_margin)
        draw.line((red_x, 0, red_x, height), fill=margin_color, width=2)
    return image

def build_ona_tili_background(size, offset_y=0):
    """Ona tili daftari uslubidagi fon yaratadi"""
    return build_ona_tili_background_side(size, offset_y, side='left')

def build_ona_tili_background_side(size, offset_y=0, side='left'):
    """Ona tili daftari uslubidagi fon yaratadi."""
    width, height = size
    image = Image.new("RGBA", size, (250, 248, 240, 255))
    draw = ImageDraw.Draw(image)

    base_step = max(20, round(width / 24))
    red_margin = base_step * 3

    line_color = (90, 160, 220, 170)
    margin_color = (211, 91, 91, 175)

    start_y = offset_y + ONA_TILI_TEMPLATE_BASE_OFFSET_Y
    while start_y > 0:
        start_y -= base_step

    # Asosiy yozuv chiziqlari.
    for y in range(start_y, height + base_step, base_step):
        draw.line((0, y, width, y), fill=line_color, width=2)

    if side != 'none':
        red_x = red_margin + ONA_TILI_RED_MARGIN_OFFSET_X
        if side == 'right':
            red_x = max(0, width - red_margin - ONA_TILI_RED_MARGIN_OFFSET_X)
        draw.line(
            (red_x, 0, red_x, height),
            fill=margin_color,
            width=2
        )

    return image

def apply_preview_layout(preview_bytes, settings):
    """Preview PNG ga tanlangan fonni qo'shadi"""
    layout = settings.get('preview_layout', 'plain')
    if layout == 'plain':
        return preview_bytes

    foreground = Image.open(BytesIO(preview_bytes)).convert("RGBA")

    if layout == 'school_graph':
        background = build_school_graph_background(
            foreground.size,
            settings.get('preview_template_offset_y', 0)
        )
    elif layout == 'school_graph_right':
        background = build_school_graph_background_side(
            foreground.size,
            settings.get('preview_template_offset_y', 0),
            side='right'
        )
    elif layout == 'school_graph_nored':
        background = build_school_graph_background_side(
            foreground.size,
            settings.get('preview_template_offset_y', 0),
            side='none'
        )
    elif layout == 'ona_tili':
        background = build_ona_tili_background(
            foreground.size,
            settings.get('preview_template_offset_y', 0)
        )
    elif layout == 'ona_tili_right':
        background = build_ona_tili_background_side(
            foreground.size,
            settings.get('preview_template_offset_y', 0),
            side='right'
        )
    elif layout == 'ona_tili_nored':
        background = build_ona_tili_background_side(
            foreground.size,
            settings.get('preview_template_offset_y', 0),
            side='none'
        )
    else:
        return preview_bytes

    composed = Image.alpha_composite(background, foreground)
    output = BytesIO()
    composed.save(output, format="PNG")
    return output.getvalue()

def build_preview_reply_markup(text_key, settings):
    """Preview uchun keyboardni quradi"""
    keyboard = [
        [InlineKeyboardButton("AI bilan realistic qilish", callback_data=f"aienhance:{text_key}")]
    ]

    if settings.get('preview_layout') == 'plain':
        auto_fit_label = "Auto Fit enabled" if settings.get('font_size') == 'auto' else "Auto Fit"
        auto_fit_callback = "af:noop" if settings.get('font_size') == 'auto' else f"af:{text_key}"
        keyboard.append([InlineKeyboardButton(auto_fit_label, callback_data=auto_fit_callback)])

    if is_notebook_preview_layout(settings.get('preview_layout')):
        current_offset = settings.get('preview_template_offset_y', 0)
        keyboard.extend([
            [
                InlineKeyboardButton("-10", callback_data=f"ps:-10:{text_key}"),
                InlineKeyboardButton(f"Shift {current_offset:+d}", callback_data="ps:noop"),
                InlineKeyboardButton("+10", callback_data=f"ps:10:{text_key}")
            ],
            [
                InlineKeyboardButton("-1", callback_data=f"ps:-1:{text_key}"),
                InlineKeyboardButton("Reset", callback_data=f"ps:reset:{text_key}"),
                InlineKeyboardButton("+1", callback_data=f"ps:1:{text_key}")
            ]
        ])

    return InlineKeyboardMarkup(keyboard)

def build_preview_shift_reply_markup(text_key):
    """Preview yuborilgandan keyingi template shift keyboardi"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("-10", callback_data=f"ps:-10:{text_key}"),
            InlineKeyboardButton("Reset", callback_data=f"ps:reset:{text_key}"),
            InlineKeyboardButton("+10", callback_data=f"ps:10:{text_key}")
        ],
        [
            InlineKeyboardButton("-1", callback_data=f"ps:-1:{text_key}"),
            InlineKeyboardButton("Offset", callback_data="ps:noop"),
            InlineKeyboardButton("+1", callback_data=f"ps:1:{text_key}")
        ]
    ])

def build_preview_shift_text(settings):
    """Preview template shift holatini matn ko'rinishida qaytaradi"""
    return (
        "Template Shift\n\n"
        f"Current offset: {settings.get('preview_template_offset_y', 0):+d}px\n"
        "Negative values move the lines up, positive values move them down."
    )

def convert_png_bytes_to_white_png_bytes(png_bytes):
    """Transparent PNG bytes ni oq fonli PNG bytes ga o'giradi"""
    image = Image.open(BytesIO(png_bytes)).convert("RGBA")
    white_background = Image.new("RGB", image.size, (255, 255, 255))
    white_background.paste(image, mask=image.getchannel("A"))
    output = BytesIO()
    white_background.save(output, format="PNG")
    return output.getvalue()

def remove_preview_watermark_png_bytes(png_bytes):
    """Docsdagi qoidaga ko'ra preview watermarkini postprocessing bilan olib tashlaydi."""
    image = Image.open(BytesIO(png_bytes)).convert("RGBA")
    pixels = image.load()
    width, height = image.size

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            # Watermark odatda semi-transparent, achromatic va kulrang bo'ladi.
            # Qo'lyozma esa rangli/kontrastli bo'lgani uchun channel spread bilan ajratamiz.
            if a <= 5 or a >= 250:
                continue
            brightness = (r + g + b) / 3.0
            channel_spread = max(r, g, b) - min(r, g, b)
            if channel_spread < 18 and brightness > 70:
                pixels[x, y] = (r, g, b, 0)

    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()

def convert_png_bytes_to_jpg_bytes(png_bytes):
    """PNG bytes ni JPG bytes ga o'giradi"""
    image = Image.open(BytesIO(png_bytes)).convert("RGBA")
    white_background = Image.new("RGB", image.size, (255, 255, 255))
    white_background.paste(image, mask=image.getchannel("A"))

    # Telegram photo yuborishda o'lcham cheklovlariga tushish uchun
    # juda katta rasmlarni proporsiyani saqlagan holda kichraytiramiz.
    max_side = 5000
    max_sum = 9900
    width, height = white_background.size
    scale = min(1.0, max_side / max(width, height), max_sum / (width + height))
    if scale < 1.0:
        resized_size = (max(1, int(width * scale)), max(1, int(height * scale)))
        white_background = white_background.resize(resized_size, Image.Resampling.LANCZOS)

    output = BytesIO()
    white_background.save(output, format="JPEG", quality=95)
    return output.getvalue()

def cleanup_expired_preview_cache(context):
    """Eskirgan preview cache yozuvlarini o'chiradi"""
    preview_images = context.bot_data.get('preview_images', {})
    preview_raw_images = context.bot_data.get('preview_raw_images', {})
    preview_meta = context.bot_data.get('preview_meta', {})
    texts = context.bot_data.get('texts', {})
    text_settings = context.bot_data.get('text_settings', {})
    now = time.time()

    expired_keys = [
        text_key for text_key, meta in preview_meta.items()
        if now - meta.get('created_at', 0) > PREVIEW_CACHE_TTL_SECONDS
    ]

    for text_key in expired_keys:
        preview_images.pop(text_key, None)
        preview_raw_images.pop(text_key, None)
        preview_meta.pop(text_key, None)
        texts.pop(text_key, None)
        text_settings.pop(text_key, None)

    pending_preview_choices = context.bot_data.get('pending_preview_choices', {})
    expired_pending_keys = [
        pending_key for pending_key, meta in pending_preview_choices.items()
        if now - meta.get('created_at', 0) > PREVIEW_CACHE_TTL_SECONDS
    ]
    for pending_key in expired_pending_keys:
        pending_preview_choices.pop(pending_key, None)

    pending_cyrillic_confirms = context.bot_data.get('pending_cyrillic_confirms', {})
    expired_confirm_keys = [
        confirm_key for confirm_key, meta in pending_cyrillic_confirms.items()
        if now - meta.get('created_at', 0) > PREVIEW_CACHE_TTL_SECONDS
    ]
    for confirm_key in expired_confirm_keys:
        pending_cyrillic_confirms.pop(confirm_key, None)

def build_notebook_side_choice_keyboard(pending_key):
    """Notebook uchun chap/o'ng tanlash keyboardi."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Left", callback_data=f"layoutchoice:left:{pending_key}"),
            InlineKeyboardButton("No Red Line", callback_data=f"layoutchoice:none:{pending_key}"),
            InlineKeyboardButton("Right", callback_data=f"layoutchoice:right:{pending_key}")
        ],
        [
            InlineKeyboardButton("Cancel", callback_data=f"layoutchoice:cancel:{pending_key}")
        ]
    ])

def get_side_preview_layout(base_layout, side):
    """Notebook layoutni chap/o'ng varianti bilan qaytaradi."""
    if base_layout == 'school_graph':
        if side == 'none':
            return 'school_graph_nored'
        return 'school_graph_right' if side == 'right' else 'school_graph'
    if base_layout == 'ona_tili':
        if side == 'none':
            return 'ona_tili_nored'
        return 'ona_tili_right' if side == 'right' else 'ona_tili'
    return base_layout

async def send_generated_preview_message(message, context, user_text, settings, api_key, text_key, user_id, reply_to_message_id=None):
    """Preview generation va yuborishni bitta oqimda bajaradi."""
    raw_preview_bytes = text_to_handwritten_preview(user_text, api_key, settings)
    cleaned_raw_preview_bytes = remove_preview_watermark_png_bytes(raw_preview_bytes)
    preview_bytes = apply_preview_layout(cleaned_raw_preview_bytes, settings)
    cleaned_preview_bytes = preview_bytes if is_notebook_preview_layout(settings.get('preview_layout')) else cleaned_raw_preview_bytes
    preview_jpg_bytes = convert_png_bytes_to_jpg_bytes(cleaned_preview_bytes)
    increment_stat(context, 'preview_count')
    log_usage_event(
        user_id,
        "preview_generated",
        {
            "text_key": text_key,
            "layout": settings.get("preview_layout", "plain"),
            "font_id": settings.get("font_id", 1),
            "font_size": settings.get("font_size", 56),
        },
    )

    if not context.bot_data.get('texts'):
        context.bot_data['texts'] = {}
    context.bot_data['texts'][text_key] = user_text
    if not context.bot_data.get('text_settings'):
        context.bot_data['text_settings'] = {}
    context.bot_data['text_settings'][text_key] = deepcopy(settings)
    if not context.bot_data.get('preview_images'):
        context.bot_data['preview_images'] = {}
    context.bot_data['preview_images'][text_key] = cleaned_preview_bytes
    if not context.bot_data.get('preview_raw_images'):
        context.bot_data['preview_raw_images'] = {}
    context.bot_data['preview_raw_images'][text_key] = cleaned_raw_preview_bytes
    if not context.bot_data.get('preview_meta'):
        context.bot_data['preview_meta'] = {}
    context.bot_data['preview_meta'][text_key] = {
        'created_at': time.time(),
        'user_id': user_id
    }

    reply_markup = build_preview_reply_markup(text_key, settings)
    photo_send_kwargs = {
        "chat_id": message.chat.id,
        "reply_markup": reply_markup,
    }
    document_send_kwargs = {
        "chat_id": message.chat.id,
    }
    if reply_to_message_id is not None:
        photo_send_kwargs["reply_to_message_id"] = reply_to_message_id
        document_send_kwargs["reply_to_message_id"] = reply_to_message_id

    await context.bot.send_photo(
        photo=preview_jpg_bytes,
        caption="Preview (Cleaned, 1200px)\n\n"
                "This preview is postprocessed to remove the watermark.",
        **photo_send_kwargs
    )
    preview_layout = settings.get('preview_layout')
    preview_document_bytes = cleaned_preview_bytes
    preview_document_caption = (
        "Clean preview PNG\n\n"
        "This is the postprocessed preview file without watermark."
    )
    if preview_layout == 'plain':
        preview_document_bytes = convert_png_bytes_to_white_png_bytes(cleaned_preview_bytes)
        preview_document_caption = (
            "Clean preview PNG (white background)\n\n"
            "This is the postprocessed preview file with white background."
        )
    await context.bot.send_document(
        document=preview_document_bytes,
        filename=f"preview_{text_key}.png",
        caption=preview_document_caption,
        reply_markup=reply_markup,
        **document_send_kwargs
    )

def build_settings_keyboard(settings):
    """Asosiy settings keyboardini quradi"""
    color_name = get_color_name(settings['text_color'])

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Font: {settings['font_id']}", callback_data="setting:font")],
        [InlineKeyboardButton(f"Font size: {settings.get('font_size', 56)} px", callback_data="setting:fontsize")],
        [InlineKeyboardButton(f"Preview Layout: {get_preview_layout_name(settings.get('preview_layout', 'plain'))}", callback_data="setting:previewlayout")],
        [InlineKeyboardButton(f"Color: {color_name}", callback_data="setting:color")],
        [InlineKeyboardButton(f"Top margin (A4): {settings.get('margin_top', 240)} px", callback_data="setting:topmargin")],
        [InlineKeyboardButton(f"Line spacing (A4): {get_effective_line_spacing_px(settings) if is_notebook_preview_layout(settings.get('preview_layout', 'plain')) else settings.get('line_spacing_adjust_px', 0.0)} px", callback_data="setting:linespacing")],
        [InlineKeyboardButton(f"Alignment: {settings['text_alignment']}", callback_data="setting:alignment")],
        [InlineKeyboardButton("Effects", callback_data="setting:effects")],
    ])

def build_settings_text(settings):
    """Asosiy settings matnini quradi"""
    color_name = get_color_name(settings['text_color'])

    return (
        "Settings:\n\n"
        f"Font: {settings['font_id']}\n"
        f"Font size: {settings.get('font_size', 56)} px\n"
        f"Preview Layout: {get_preview_layout_name(settings.get('preview_layout', 'plain'))}\n"
        f"Color: {color_name}\n"
        f"Top margin (A4): {settings.get('margin_top', 240)} px\n"
        f"Text: {settings['text_alignment']}\n"
        f"Line spacing (A4): {get_effective_line_spacing_px(settings) if is_notebook_preview_layout(settings.get('preview_layout', 'plain')) else settings.get('line_spacing_adjust_px', 0.0)} px\n"
        f"Paragraph spacing: {get_effective_paragraph_spacing(settings)} px\n\n"
        f"Note: A4 layout uses these values directly. Notebook layouts use tuned size/template rules.\n\n"
        "Tap a button to change a setting:"
    )

def build_font_margin_settings_text(settings):
    """Font margin tuning matnini quradi"""
    target_font_id = settings.get('font_margin_target_font_id', settings.get('font_id', 1))
    current_font_id = settings.get('font_id', 1)
    user_offset = get_user_font_top_margin_override(settings, target_font_id)
    effective_offsets = [
        f"{font_size}px: {get_effective_font_top_margin_offset(settings, target_font_id, font_size)}"
        for font_size in (56, 64, 72, 80)
    ]
    return (
        f"Font margin tuning\n\n"
        f"Selected font: {target_font_id}\n"
        f"Current active font: {current_font_id}\n"
        f"User offset: {user_offset:+d}px\n"
        f"Effective top margins: {', '.join(effective_offsets)}\n\n"
        "Use the buttons below to shift the selected font's top margin."
    )

def build_font_margin_settings_keyboard(settings):
    """Font margin tuning keyboardini quradi"""
    target_font_id = settings.get('font_margin_target_font_id', settings.get('font_id', 1))
    prev_font = 90 if target_font_id <= 1 else target_font_id - 1
    next_font = 1 if target_font_id >= 90 else target_font_id + 1
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"< {prev_font}", callback_data="fontmargin:target_prev"),
            InlineKeyboardButton(f"Font {target_font_id}", callback_data="fontmargin:use_current"),
            InlineKeyboardButton(f"{next_font} >", callback_data="fontmargin:target_next")
        ],
        [
            InlineKeyboardButton("-10", callback_data="fontmargin:adjust:-10"),
            InlineKeyboardButton("-5", callback_data="fontmargin:adjust:-5"),
            InlineKeyboardButton("-1", callback_data="fontmargin:adjust:-1")
        ],
        [
            InlineKeyboardButton("+1", callback_data="fontmargin:adjust:1"),
            InlineKeyboardButton("+5", callback_data="fontmargin:adjust:5"),
            InlineKeyboardButton("+10", callback_data="fontmargin:adjust:10")
        ],
        [
            InlineKeyboardButton("Reset", callback_data="fontmargin:reset"),
            InlineKeyboardButton("Use active font", callback_data="fontmargin:sync_current")
        ],
        [InlineKeyboardButton("Back", callback_data="setting:back")]
    ])

async def show_font_margin_settings(query, settings):
    """Font margin tuning menyusini ko'rsatadi"""
    await query.edit_message_text(
        build_font_margin_settings_text(settings),
        reply_markup=build_font_margin_settings_keyboard(settings)
    )

async def show_margin_spacing_settings(query, settings):
    """Margin va line spacing menyusini ko'rsatadi"""
    current_margin = settings.get('margin_left', 240)
    current_line_height = settings.get('line_height_multiplier', 2.0)
    current_paragraph_spacing = get_effective_paragraph_spacing(settings)
    keyboard = [
        [InlineKeyboardButton("Narrow margin (160px)", callback_data="setval:margin:160")],
        [InlineKeyboardButton("Standard margin (240px)", callback_data="setval:margin:240")],
        [InlineKeyboardButton("Wide margin (320px)", callback_data="setval:margin:320")],
        [InlineKeyboardButton("Tight lines (1.6)", callback_data="setval:lineheight:1.6")],
        [InlineKeyboardButton("Standard lines (2.0)", callback_data="setval:lineheight:2.0")],
        [InlineKeyboardButton("Wide lines (2.4)", callback_data="setval:lineheight:2.4")],
        [InlineKeyboardButton("Paragraph 40px", callback_data="setval:paragraphspacing:40")],
        [InlineKeyboardButton("Paragraph 80px", callback_data="setval:paragraphspacing:80")],
        [InlineKeyboardButton("Paragraph 120px", callback_data="setval:paragraphspacing:120")],
        [InlineKeyboardButton("Back", callback_data="setting:back")]
    ]
    await query.edit_message_text(
        f"Choose margin and spacing:\n\n"
        f"Current margin: {current_margin}px\n"
        f"Current line spacing: {current_line_height}\n"
        f"Current paragraph spacing: {current_paragraph_spacing}px\n\n"
        f"For notebook layouts, this is aligned to the grid rhythm.\n"
        f"Paragraph spacing only applies when the text contains a blank line (\\n\\n).",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_template_shift_settings(query, settings):
    """School graph preview template shift menyusini ko'rsatadi"""
    current_offset = settings.get('preview_template_offset_y', 0)
    keyboard = [
        [
            InlineKeyboardButton("-10", callback_data="templateshift:adjust:-10"),
            InlineKeyboardButton("-1", callback_data="templateshift:adjust:-1")
        ],
        [
            InlineKeyboardButton("+1", callback_data="templateshift:adjust:1"),
            InlineKeyboardButton("+10", callback_data="templateshift:adjust:10")
        ],
        [
            InlineKeyboardButton("Reset", callback_data="templateshift:reset"),
            InlineKeyboardButton("Back", callback_data="setting:back")
        ]
    ]
    await query.edit_message_text(
        f"School Graph template shift\n\n"
        f"Current offset: {current_offset:+d}px\n\n"
        f"Negative values move the lines up, positive values move them down.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_top_margin_settings(query, settings):
    """Show the global top margin settings menu."""
    current_top_margin = settings.get('margin_top', 240)
    keyboard = [
        [
            InlineKeyboardButton("-40", callback_data="topmargin:adjust:-40"),
            InlineKeyboardButton("-20", callback_data="topmargin:adjust:-20"),
            InlineKeyboardButton("-10", callback_data="topmargin:adjust:-10")
        ],
        [
            InlineKeyboardButton("+10", callback_data="topmargin:adjust:10"),
            InlineKeyboardButton("+20", callback_data="topmargin:adjust:20"),
            InlineKeyboardButton("+40", callback_data="topmargin:adjust:40")
        ],
        [
            InlineKeyboardButton("Reset", callback_data="topmargin:reset"),
            InlineKeyboardButton("Back", callback_data="setting:back")
        ]
    ]
    await query.edit_message_text(
        f"Top margin (A4)\n\n"
        f"Current top margin: {current_top_margin}px\n\n"
        f"Smaller values move text upward. Larger values move text downward.\n"
        f"This controls the global margin_top setting for A4 layout.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_line_spacing_settings(query, settings):
    """Show the global line spacing settings menu."""
    current_line_spacing = settings.get('line_spacing_adjust_px', 0.0)
    effective_line_spacing = get_effective_line_spacing_px(settings)
    keyboard = [
        [
            InlineKeyboardButton("-3", callback_data="linespacing:adjust:-3"),
            InlineKeyboardButton("-1", callback_data="linespacing:adjust:-1"),
            InlineKeyboardButton("-0.5", callback_data="linespacing:adjust:-0.5")
        ],
        [
            InlineKeyboardButton("+0.5", callback_data="linespacing:adjust:0.5"),
            InlineKeyboardButton("+1", callback_data="linespacing:adjust:1"),
            InlineKeyboardButton("+3", callback_data="linespacing:adjust:3")
        ],
        [
            InlineKeyboardButton("Reset", callback_data="linespacing:reset"),
            InlineKeyboardButton("Back", callback_data="setting:back")
        ]
    ]
    await query.edit_message_text(
        f"Line spacing (A4)\n\n"
        f"Current adjustment: {current_line_spacing:+g}px\n"
        f"Effective line spacing: {effective_line_spacing if effective_line_spacing is not None else current_line_spacing:g}px\n\n"
        f"Smaller values tighten the lines. Larger values add more space.\n"
        f"This control uses pixels for A4 layout.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_settings_message(target, settings):
    """Settings menyusini reply yoki edit orqali ko'rsatadi"""
    text = build_settings_text(settings)
    reply_markup = build_settings_keyboard(settings)

    if hasattr(target, "edit_message_text"):
        await target.edit_message_text(text, reply_markup=reply_markup)
    else:
        await target.reply_text(text, reply_markup=reply_markup)

# HandText AI API orqali WATERMARKED PREVIEW yaratish
def text_to_handwritten_preview(text: str, api_key: str, settings: dict) -> bytes:
    """
    HandText AI API orqali text ni watermarked PREVIEW ga o'zgartiradi
    Preview: Watermark bilan, 1200px kenglik, API limitdan hisoblanmaydi
    
    Args:
        text: Handwritten ga o'zgartirilishi kerak bo'lgan text
        api_key: HandText AI API kaliti (htext_... format)
        font_id: Font ID (1-90), default: 1
    
    Returns:
        bytes: PNG formatidagi watermarked preview rasm
    """
    # HandText AI PREVIEW endpoint (watermarked, 1200px)
    api_url = "https://api.handtextai.com/api/v1/preview"
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    preview_settings = get_effective_preview_settings(settings)

    # API parametrlari (preview uchun)
    # Preview har doim: PNG, 300 DPI
    payload = {
        "text": text,
        "font_id": preview_settings.get('font_id', 1),
        "font_size": preview_settings.get('font_size', 56),
        "page_size": preview_settings.get('page_size', 'a4'),
        "dpi": 300,  # Preview har doim 300 DPI
        "output_format": "png",  # Preview har doim PNG
        "text_color": preview_settings.get('text_color', [0, 0, 255]),
        "text_alignment": preview_settings.get('text_alignment', 'left'),
        "margin_left": preview_settings.get('margin_left', 240),
        "margin_right": preview_settings.get('margin_right', 240),
        "margin_top": preview_settings.get('margin_top', 240),
        "margin_bottom": preview_settings.get('margin_bottom', 240),
        "line_height_multiplier": preview_settings.get('line_height_multiplier', 2.0),
        "enable_word_rotation": preview_settings.get('enable_word_rotation', False),
        "word_rotation_range": preview_settings.get('word_rotation_range', 5.0),
        "enable_natural_variation": preview_settings.get('enable_natural_variation', False),
        "natural_variation_alpha": preview_settings.get('natural_variation_alpha', 15),
        "natural_variation_sigma": preview_settings.get('natural_variation_sigma', 5),
        "enable_ink_flow": preview_settings.get('enable_ink_flow', False),
        "ink_flow_intensity": preview_settings.get('ink_flow_intensity', 1.0)
    }

    if has_paragraph_breaks(text):
        payload["line_break_spacing"] = get_effective_paragraph_spacing(preview_settings)
    
    try:
        logger.info("Sending HandText AI PREVIEW request (watermarked)...")
        response = requests.post(api_url, json=payload, headers=headers, timeout=90)
        
        if response.status_code == 200:
            # Response JSON formatda, image_base64 ni olish kerak
            result = response.json()
            image_base64 = result.get('image_base64')
            if not image_base64:
                raise Exception("image_base64 was not found in the response")
            
            # Base64 ni bytes ga o'zgartirish
            image_bytes = base64.b64decode(image_base64)
            logger.info("Watermarked preview created successfully (1200px, free of API quota)")
            return image_bytes
        elif response.status_code == 401:
            logger.error("API key is invalid")
            raise Exception("The API key is invalid. Please check your API key.")
        elif response.status_code == 429:
            logger.error("API limit reached")
            raise Exception("API limit reached. Please try again later.")
        else:
            logger.error(f"API returned an error: {response.status_code} - {response.text}")
            raise Exception(f"API error: {response.status_code}")
            
    except requests.exceptions.Timeout:
        logger.error("API request timed out")
        raise Exception("The API request took too long. Please try again.")
    except requests.exceptions.RequestException as e:
        logger.error(f"API request error: {e}")
        raise Exception(f"Network error: {str(e)}")

# /start komandasi
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot ishga tushganda yuboriladi"""
    register_user_activity(context, update.message.from_user)
    try:
        commands = get_bot_commands()
        await context.bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await context.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())
        await context.bot.set_my_commands(commands, scope=BotCommandScopeDefault(), language_code="")
        await context.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats(), language_code="")
    except Exception as exc:
        logger.error(f"Failed to refresh bot commands in /start: {exc}")
    await update.message.reply_text(
        "Hello!\n\n"
        "I turn text into realistic handwritten writing.\n\n"
        "How it works:\n"
        "1. Send me text\n"
        "2. Get a free preview\n"
        "3. Adjust layout if needed\n\n"
        "There are 90 handwritten fonts available.\n"
        "Blue ink, preview-only mode.\n\n"
        "Commands:\n"
        "/font [number] - Choose a font (1-90)\n"
        "/size [56|64|72|80|88|96|104|112] - Choose font size\n"
        "/set - Open settings\n"
        "/color - Choose text color\n"
        "/aiedit <prompt> - Edit last image with AI\n"
        "/fonts - Preview fonts\n"
        "/balance - View credit balance\n"
        "/buy - Open credit packages\n"
        "/synccommands - Refresh slash command menu",
        reply_markup=build_main_menu_keyboard()
    )

async def ai_editor_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """AI editor bo'yicha qisqa yo'riqnoma."""
    register_user_activity(context, update.message.from_user)
    await update.message.reply_text(build_ai_editor_help_text())

async def remember_latest_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi yuborgan so'nggi rasmni AI editor uchun saqlab qo'yadi."""
    register_user_activity(context, update.message.from_user)
    image_ref = await extract_image_reference_from_message(update.message)
    if not image_ref:
        return

    context.user_data['ai_editor_last_image_ref'] = {
        **image_ref,
        "saved_at": time.time(),
    }
    await update.message.reply_text(
        "Rasm AI editor uchun saqlandi. Endi /aiedit <prompt> yuboring."
    )

async def ai_edit_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """So'nggi yoki reply qilingan rasmni Gemini orqali tahrirlaydi."""
    register_user_activity(context, update.message.from_user)

    prompt = " ".join(context.args).strip()
    if not prompt:
        await update.message.reply_text(
            "Prompt kiriting. Misol:\n/aiedit Make this look like a realistic phone photo on a wooden table."
        )
        return

    image_ref = None
    if update.message.reply_to_message:
        image_ref = await extract_image_reference_from_message(update.message.reply_to_message)
        if image_ref:
            context.user_data['ai_editor_last_image_ref'] = {
                **image_ref,
                "saved_at": time.time(),
            }

    if not image_ref:
        image_ref = context.user_data.get('ai_editor_last_image_ref')

    if not image_ref:
        await update.message.reply_text(
            "Avval rasm yuboring, keyin /aiedit <prompt> ishlating."
        )
        return

    api_key = get_gemini_image_api_key()
    if not api_key:
        await update.message.reply_text(
            "GEMINI_API_KEY topilmadi. .env ga GEMINI_API_KEY qo'shing."
        )
        return

    model = get_gemini_image_model()

    try:
        await update.message.chat.send_action("upload_photo")
        source_bytes = await download_telegram_file_bytes(context, image_ref["file_id"])
        result_bytes, result_mime = await asyncio.to_thread(
            call_gemini_image_edit,
            prompt,
            source_bytes,
            image_ref.get("mime_type", "image/jpeg"),
            api_key,
            model,
        )
        telegram_photo_bytes = get_telegram_photo_bytes_for_ai_result(result_bytes, result_mime)

        await update.message.reply_photo(
            photo=telegram_photo_bytes,
            caption=f"AI edited ({model})",
            connect_timeout=30,
            read_timeout=120,
            write_timeout=120,
            pool_timeout=60,
        )

        extension = "png"
        if result_mime.endswith("jpeg") or result_mime.endswith("jpg"):
            extension = "jpg"
        try:
            await update.message.reply_document(
                document=result_bytes,
                filename=f"ai_edit_result.{extension}",
                caption="AI editor file",
                connect_timeout=30,
                read_timeout=120,
                write_timeout=120,
                pool_timeout=60,
            )
        except Exception as doc_exc:
            logger.warning(f"AI result document send failed: {doc_exc}")
    except Exception as exc:
        logger.error(f"AI editor error: {exc}")
        await update.message.reply_text(f"AI editor xatoligi: {exc}")

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin buyruqlari haqida ma'lumot beradi"""
    user = update.message.from_user
    register_user_activity(context, user)

    if not is_admin(user.id):
        await update.message.reply_text("This command is for admins only.")
        return

    await update.message.reply_text(
        "Admin commands:\n\n"
        "/admin - admin menu\n"
        "/stats - bot statistics\n"
        "/addcredit <user_id> <amount> - update credits\n\n"
        "Preview-only mode is active.\n\n"
        "Set ADMIN_USER_IDS in .env as a comma-separated list."
    )

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun bot statistikalarini ko'rsatadi"""
    user = update.message.from_user
    register_user_activity(context, user)

    if not is_admin(user.id):
        await update.message.reply_text("This command is for admins only.")
        return

    stats = context.bot_data.get('stats', {})
    known_users = context.bot_data.get('known_users', {})
    activity_dates = context.bot_data.get('activity_dates', {})
    today = time.strftime("%Y-%m-%d")
    active_today = sum(1 for last_seen in activity_dates.values() if last_seen == today)

    await update.message.reply_text(
        "Bot statistics:\n\n"
        f"Total users: {len(known_users)}\n"
        f"Active today: {active_today}\n"
        f"Preview count: {stats.get('preview_count', 0)}\n"
        f"Auto-fit preview count: {stats.get('auto_fit_preview_count', 0)}\n"
        "Preview-only mode is active."
    )

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchining credit balansini ko'rsatadi."""
    user = update.message.from_user
    register_user_activity(context, user)

    if _get_firestore_client() is None:
        await update.message.reply_text("Balance is unavailable now. Firestore is not configured.")
        return

    balance = get_user_balance_credits(user.id)
    await update.message.reply_text(f"Your balance: {balance} credits")

async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Credit paketlari menyusini ko'rsatadi."""
    user = update.message.from_user
    register_user_activity(context, user)
    await update.message.reply_text(
        "Choose a credit package:",
        reply_markup=build_buy_packages_keyboard()
    )

async def admin_add_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun userga credit qo'shish."""
    user = update.message.from_user
    register_user_activity(context, user)

    if not is_admin(user.id):
        await update.message.reply_text("This command is for admins only.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /addcredit <user_id> <amount>")
        return

    try:
        target_user_id = int(context.args[0])
        delta = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Invalid format. Usage: /addcredit <user_id> <amount>")
        return

    if _get_firestore_client() is None:
        await update.message.reply_text("Firestore is not configured.")
        return

    try:
        new_balance = add_user_credits(
            target_user_id,
            delta,
            actor_user_id=user.id,
            reason="admin_addcredit",
        )
    except Exception as exc:
        await update.message.reply_text(f"Failed to update credits: {exc}")
        return

    await update.message.reply_text(
        f"Updated user {target_user_id} by {delta:+d} credits.\nNew balance: {new_balance}"
    )

# /font komandasi - font tanlash
async def set_font(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi font tanlaydi"""
    if context.args and len(context.args) > 0:
        try:
            font_id = int(context.args[0])
            if 1 <= font_id <= 90:
                user_id = update.message.from_user.id
                update_user_setting(context, user_id, 'font_id', font_id)
                await update.message.reply_text(
                    f"Font {font_id} selected. Now send some text and try it."
                )
            else:
                await update.message.reply_text(
                    "Font ID must be between 1 and 90.\n"
                    "Example: /font 5"
                )
        except ValueError:
            await update.message.reply_text(
                "Invalid format. Example: /font 5"
            )
    else:
        current_font = get_user_settings(context, update.message.from_user.id)['font_id']
        await update.message.reply_text(
            f"Current font: {current_font}\n\n"
            "Change font: /font [1-90]\n"
            "Example: /font 5\n\n"
            "Recommended: Fonts 1 to 11"
        )

# /fonts komandasi - fontlarni ko'rish
async def show_fonts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mashhur fontlarni ko'rsatish"""
    keyboard = [
        [
            InlineKeyboardButton("Font 1", callback_data="testfont:1"),
            InlineKeyboardButton("Font 2", callback_data="testfont:2"),
            InlineKeyboardButton("Font 3", callback_data="testfont:3")
        ],
        [
            InlineKeyboardButton("Font 4", callback_data="testfont:4"),
            InlineKeyboardButton("Font 5", callback_data="testfont:5"),
            InlineKeyboardButton("Font 6", callback_data="testfont:6")
        ],
        [
            InlineKeyboardButton("Font 7", callback_data="testfont:7"),
            InlineKeyboardButton("Font 8", callback_data="testfont:8"),
            InlineKeyboardButton("Font 9", callback_data="testfont:9")
        ],
        [
            InlineKeyboardButton("Font 10", callback_data="testfont:10"),
            InlineKeyboardButton("Font 11", callback_data="testfont:11")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Try the fonts!\n\n"
        "Tap a button to see a sample.\n"
        "If you like one, set it with /font [number].\n\n"
        "Recommended: Fonts 1 to 11 (max-size sample)",
        reply_markup=reply_markup
    )

# /size komandasi - shrift o'lchamini tanlash
async def set_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi shrift o'lchamini tanlaydi"""
    available_sizes = AVAILABLE_FONT_SIZES

    if context.args and len(context.args) > 0:
        try:
            font_size = int(context.args[0])
            if font_size in available_sizes:
                user_id = update.message.from_user.id
                update_user_setting(context, user_id, 'font_size', font_size)
                await update.message.reply_text(
                    f"Font size set to {font_size}px. Now send text and try it."
                )
            else:
                await update.message.reply_text(
                    "Only these sizes are supported: 56, 64, 72, 80, 88, 96, 104, 112.\n"
                    "Example: /size 72"
                )
        except ValueError:
            await update.message.reply_text(
                "Invalid format. Example: /size 72"
            )
    else:
        current_size = get_user_settings(context, update.message.from_user.id).get('font_size', 56)
        await update.message.reply_text(
            f"Current font size: {current_size}px\n\n"
            "Change it with: /size [56|64|72|80|88|96|104|112]\n"
            "Example: /size 72"
        )

# /set komandasi - sozlamalarni ko'rish va o'zgartirish
async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sozlamalar menyusini ko'rsatish"""
    register_user_activity(context, update.message.from_user)
    user_id = update.message.from_user.id
    settings = get_user_settings(context, user_id)
    await show_settings_message(update.message, settings)

# /color komandasi - rang tanlash
async def set_color(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi rang tanlaydi"""
    register_user_activity(context, update.message.from_user)
    keyboard = [
        [
            InlineKeyboardButton("Blue", callback_data="color:0,0,255"),
            InlineKeyboardButton("Black", callback_data="color:0,0,0")
        ],
        [
            InlineKeyboardButton("Red", callback_data="color:255,0,0"),
            InlineKeyboardButton("Green", callback_data="color:0,128,0")
        ],
        [
            InlineKeyboardButton("Dark blue", callback_data="color:0,0,139"),
            InlineKeyboardButton("Purple", callback_data="color:128,0,128")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    current_color = get_user_settings(context, update.message.from_user.id)['text_color']
    await update.message.reply_text(
        f"Current color: RGB{current_color}\n\n"
        "Choose a color:",
        reply_markup=reply_markup
    )

# Text xabarlarni qayta ishlash
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi yuborgan textni qayta ishlaydi"""
    register_user_activity(context, update.message.from_user)
    user_text = update.message.text
    
    if not user_text:
        await update.message.reply_text("Please send some text.")
        return

    normalized = user_text.strip().lower()
    if normalized == "balance":
        await show_balance(update, context)
        return
    if normalized == "buy credits":
        await buy_menu(update, context)
        return
    if normalized == "settings":
        await settings_menu(update, context)
        return
    # API key ni olish
    api_key = os.getenv('HANTEXT_API_KEY')
    if not api_key:
        logger.error("HANTEXT_API_KEY not found")
        await update.message.reply_text(
            "The bot is not configured. Please contact the administrator."
        )
        return
    
    # "Typing..." ko'rsatish
    await update.message.chat.send_action("upload_photo")
    
    try:
        cleanup_expired_preview_cache(context)

        # Foydalanuvchining sozlamalarini olish
        user_id = update.message.from_user.id
        settings = get_user_settings(context, user_id)
        text_key = f"{user_id}_{update.message.message_id}"

        if contains_cyrillic_text(user_text) and settings.get('font_id') not in CYRILLIC_SUPPORTED_FONT_IDS:
            confirm_key = f"{user_id}_{update.message.message_id}_{int(time.time())}"
            if not context.bot_data.get('pending_cyrillic_confirms'):
                context.bot_data['pending_cyrillic_confirms'] = {}
            context.bot_data['pending_cyrillic_confirms'][confirm_key] = {
                'user_text': user_text,
                'settings': deepcopy(settings),
                'user_id': user_id,
                'text_key': text_key,
                'created_at': time.time(),
                'source_message_id': update.message.message_id,
            }
            await update.message.reply_text(
                "Tanlangan shriftda kirill harflari qo'llab-quvvatlanmaydi.\n"
                "Davom etasizmi?\n\n"
                f"Kirill support shriftlari: {get_cyrillic_supported_fonts_text()}",
                reply_markup=build_cyrillic_confirmation_keyboard(confirm_key)
            )
            return

        await start_preview_flow(
            update.message,
            context,
            user_text,
            settings,
            api_key,
            text_key,
            user_id,
            reply_to_message_id=update.message.message_id
        )
        return

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        await update.message.reply_text(
            f"Sorry, an error occurred: {str(e)}\n\n"
            "Please try again."
        )

# Callback query handler (tugma bosilganda)
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline keyboard tugmasi bosilganda ishlaydi"""
    query = update.callback_query
    await query.answer()
    register_user_activity(context, query.from_user)
    
    # Callback data ni parse qilish
    data = query.data
    user_id = query.from_user.id

    if data.startswith("buypkg:"):
        _, credits_raw, amount_raw = data.split(":", 2)
        payment_contact = os.getenv("PAYMENT_CONTACT", "").strip()
        payment_link = os.getenv("PAYMENT_LINK", "").strip()
        message = (
            f"Package selected: {credits_raw} credits\n"
            f"Amount: {amount_raw} UZS\n\n"
            "Send payment and then send your receipt to admin."
        )
        if payment_contact:
            message += f"\nPayment contact: {payment_contact}"
        if payment_link:
            message += f"\nPayment link: {payment_link}"
        message += "\nAfter payment, send your receipt to admin."
        await query.message.reply_text(message)
        return

    if data.startswith("aienhance:"):
        cleanup_expired_preview_cache(context)
        text_key = data.split(":", 1)[1]
        preview_meta = context.bot_data.get('preview_meta', {}).get(text_key)
        if not preview_meta:
            await query.message.reply_text("Preview eskirgan. Matnni qayta yuboring.")
            return
        if preview_meta.get('user_id') != user_id:
            await query.message.reply_text("Bu preview sizga tegishli emas.")
            return

        file_id = None
        file_mime = "image/jpeg"
        if query.message and query.message.photo:
            file_id = query.message.photo[-1].file_id
            file_mime = "image/jpeg"
        elif query.message and query.message.document and (query.message.document.mime_type or "").startswith("image/"):
            file_id = query.message.document.file_id
            file_mime = query.message.document.mime_type or "image/png"
        if not file_id:
            await query.message.reply_text("AI tugmasini preview rasm yoki PNG fayl xabari ustidan bosing.")
            return

        layout = context.bot_data.get('text_settings', {}).get(text_key, {}).get('preview_layout', 'plain')

        api_key = get_gemini_image_api_key()
        if not api_key:
            await query.message.reply_text("GEMINI_API_KEY topilmadi.")
            return

        model = get_gemini_image_model()
        try:
            await query.message.chat.send_action("upload_photo")
            source_bytes = await download_telegram_file_bytes(context, file_id)
            result_bytes, result_mime = await asyncio.to_thread(
                call_gemini_image_edit,
                get_default_ai_realistic_prompt(layout),
                source_bytes,
                file_mime,
                api_key,
                model,
            )
            telegram_photo_bytes = get_telegram_photo_bytes_for_ai_result(result_bytes, result_mime)
            await query.message.reply_photo(
                photo=telegram_photo_bytes,
                caption=f"AI realistic natija ({model})",
                connect_timeout=30,
                read_timeout=120,
                write_timeout=120,
                pool_timeout=60,
            )
            extension = "png"
            if result_mime.endswith("jpeg") or result_mime.endswith("jpg"):
                extension = "jpg"
            try:
                await query.message.reply_document(
                    document=result_bytes,
                    filename=f"ai_realistic_{text_key}.{extension}",
                    caption="AI realistic file",
                    connect_timeout=30,
                    read_timeout=120,
                    write_timeout=120,
                    pool_timeout=60,
                )
            except Exception as doc_exc:
                logger.warning(f"AI realistic document send failed: {doc_exc}")
        except Exception as exc:
            logger.error(f"AI realistic callback error: {exc}")
            await query.message.reply_text(f"AI edit xatoligi: {exc}")
        return

    if data.startswith("cyrconfirm:"):
        cleanup_expired_preview_cache(context)
        _, decision, confirm_key = data.split(":", 2)
        pending_cyrillic_confirms = context.bot_data.get('pending_cyrillic_confirms', {})
        pending_item = pending_cyrillic_confirms.pop(confirm_key, None)

        if not pending_item:
            await query.message.reply_text("Tasdiqlash muddati tugagan. Matnni qayta yuboring.")
            return

        if decision == "no":
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await query.message.reply_text("Bekor qilindi. Yangi matn yuborishingiz mumkin.")
            return

        if decision != "yes":
            await query.message.reply_text("Noto'g'ri amal.")
            return

        api_key = os.getenv('HANTEXT_API_KEY')
        if not api_key:
            await query.message.reply_text("The bot is not configured.")
            return

        try:
            await query.message.delete()
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass

        await start_preview_flow(
            query.message,
            context,
            pending_item['user_text'],
            deepcopy(pending_item['settings']),
            api_key,
            pending_item['text_key'],
            pending_item['user_id'],
            reply_to_message_id=pending_item.get('source_message_id')
        )
        return

    if data.startswith("layoutchoice:"):
        cleanup_expired_preview_cache(context)
        _, side, pending_key = data.split(":", 2)
        pending_preview_choices = context.bot_data.get('pending_preview_choices', {})
        pending_item = pending_preview_choices.pop(pending_key, None)

        if not pending_item:
            await query.message.reply_text("Preview request expired. Please send the text again.")
            return

        if side == "cancel":
            try:
                await query.message.delete()
            except Exception:
                try:
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
            await query.answer("Notebook side selection canceled.")
            return

        api_key = os.getenv('HANTEXT_API_KEY')
        if not api_key:
            await query.message.reply_text("The bot is not configured.")
            return

        base_settings = deepcopy(pending_item['settings'])
        base_settings['preview_layout'] = get_side_preview_layout(base_settings.get('preview_layout', 'plain'), side)
        text_key = pending_item['text_key']
        user_text = pending_item['user_text']
        try:
            await query.message.delete()
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        await query.answer("Generating preview...")
        await send_generated_preview_message(
            query.message,
            context,
            user_text,
            base_settings,
            api_key,
            text_key,
            pending_item['user_id'],
            reply_to_message_id=None
        )
        return

    if data == "ps:noop":
        return

    if data == "af:noop":
        return
    
    # Settings - Page size tanlash
    if data == "setting:pagesize":
        keyboard = [
            [InlineKeyboardButton("A4", callback_data="setval:pagesize:a4"),
             InlineKeyboardButton("A5", callback_data="setval:pagesize:a5")],
            [InlineKeyboardButton("Letter", callback_data="setval:pagesize:letter"),
             InlineKeyboardButton("Postcard", callback_data="setval:pagesize:postcard_us")],
            [InlineKeyboardButton("Card 5x7", callback_data="setval:pagesize:card_5x7"),
             InlineKeyboardButton("Card A2", callback_data="setval:pagesize:card_a2")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose a page size:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    if data == "setting:font":
        keyboard = [
            [InlineKeyboardButton("1", callback_data="setval:font:1"),
             InlineKeyboardButton("2", callback_data="setval:font:2"),
             InlineKeyboardButton("3", callback_data="setval:font:3")],
            [InlineKeyboardButton("5", callback_data="setval:font:5"),
             InlineKeyboardButton("10", callback_data="setval:font:10"),
             InlineKeyboardButton("15", callback_data="setval:font:15")],
            [InlineKeyboardButton("20", callback_data="setval:font:20"),
             InlineKeyboardButton("25", callback_data="setval:font:25"),
             InlineKeyboardButton("30", callback_data="setval:font:30")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose a font:\n\nFor more options, use /fonts or /font [1-90].",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "setting:fontsize":
        keyboard = [
            [InlineKeyboardButton("56 px", callback_data="setval:fontsize:56"),
             InlineKeyboardButton("64 px", callback_data="setval:fontsize:64")],
            [InlineKeyboardButton("72 px", callback_data="setval:fontsize:72"),
             InlineKeyboardButton("80 px", callback_data="setval:fontsize:80")],
            [InlineKeyboardButton("88 px", callback_data="setval:fontsize:88"),
             InlineKeyboardButton("96 px", callback_data="setval:fontsize:96")],
            [InlineKeyboardButton("104 px", callback_data="setval:fontsize:104"),
             InlineKeyboardButton("112 px", callback_data="setval:fontsize:112")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose a font size:\n\nAvailable sizes: 56 px, 64 px, 72 px, 80 px, 88 px, 96 px, 104 px, 112 px.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "setting:topmargin":
        settings = get_user_settings(context, user_id)
        await show_top_margin_settings(query, settings)
        return

    if data == "setting:linespacing":
        settings = get_user_settings(context, user_id)
        await show_line_spacing_settings(query, settings)
        return

    if data == "setting:previewlayout":
        keyboard = [
            [InlineKeyboardButton("A4", callback_data="setval:previewlayout:plain")],
            [InlineKeyboardButton("Math Notebook", callback_data="setval:previewlayout:school_graph")],
            [InlineKeyboardButton("Language Notebook", callback_data="setval:previewlayout:ona_tili")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose a preview layout:\n\nA4 is fixed for the plain layout.\n"
            "Right notebook variants mirror the red margin to the right side.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "setting:templateshift":
        settings = get_user_settings(context, user_id)
        await show_template_shift_settings(query, settings)
        return

    if data == "setting:color":
        keyboard = [
            [InlineKeyboardButton("Blue", callback_data="setval:color:0,0,255"),
             InlineKeyboardButton("Black", callback_data="setval:color:0,0,0")],
            [InlineKeyboardButton("Red", callback_data="setval:color:255,0,0"),
             InlineKeyboardButton("Green", callback_data="setval:color:0,128,0")],
            [InlineKeyboardButton("Dark blue", callback_data="setval:color:0,0,139"),
             InlineKeyboardButton("Purple", callback_data="setval:color:128,0,128")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose a color:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Settings - Alignment tanlash
    if data == "setting:alignment":
        keyboard = [
            [InlineKeyboardButton("Left", callback_data="setval:alignment:left")],
            [InlineKeyboardButton("Center", callback_data="setval:alignment:center")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose text alignment:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return
    
    # Settings - Effektlar
    if data == "setting:effects":
        settings = get_user_settings(context, user_id)
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅' if settings['enable_word_rotation'] else '❌'} Word Rotation",
                callback_data="toggle:word_rotation"
            )],
            [InlineKeyboardButton(
                f"{'✅' if settings['enable_natural_variation'] else '❌'} Natural Variation",
                callback_data="toggle:natural_variation"
            )],
            [InlineKeyboardButton(
                f"{'✅' if settings['enable_ink_flow'] else '❌'} Ink Flow",
                callback_data="toggle:ink_flow"
            )],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Natural effects:\n\n"
            "Word Rotation - slight word rotation\n"
            "Natural Variation - more organic variation\n"
            "Ink Flow - fading/running ink effect",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "setting:fontmargins":
        settings = get_user_settings(context, user_id)
        await show_font_margin_settings(query, settings)
        return

    if data == "setting:margins":
        settings = get_user_settings(context, user_id)
        await show_margin_spacing_settings(query, settings)
        return
    
    # Settings - Reset
    if data == "setting:reset":
        context.bot_data['user_settings'][user_id] = deepcopy(DEFAULT_SETTINGS)
        await query.answer("Settings reset to defaults!")
        await query.message.reply_text("Settings were reset to defaults. Use /set")
        return
    
    # Settings - Orqaga
    if data == "setting:back":
        settings = get_user_settings(context, user_id)
        await show_settings_message(query, settings)
        return

    if data.startswith("fontmargin:"):
        settings = get_user_settings(context, user_id)
        action = data.replace("fontmargin:", "")
        target_font_id = settings.get('font_margin_target_font_id', settings.get('font_id', 1))

        if action == "target_prev":
            target_font_id = 90 if target_font_id <= 1 else target_font_id - 1
            settings['font_margin_target_font_id'] = target_font_id
        elif action == "target_next":
            target_font_id = 1 if target_font_id >= 90 else target_font_id + 1
            settings['font_margin_target_font_id'] = target_font_id
        elif action == "sync_current" or action == "use_current":
            settings['font_margin_target_font_id'] = settings.get('font_id', 1)
        elif action == "reset":
            set_user_font_top_margin_override(settings, target_font_id, 0)
            await query.answer(f"Font {target_font_id} override reset qilindi")
        elif action.startswith("adjust:"):
            delta = int(action.split(":")[1])
            current_value = get_user_font_top_margin_override(settings, target_font_id)
            new_value = current_value + delta
            set_user_font_top_margin_override(settings, target_font_id, new_value)
            await query.answer(f"Font {target_font_id}: {new_value:+d}px")

        await show_font_margin_settings(query, settings)
        return

    if data.startswith("templateshift:"):
        settings = get_user_settings(context, user_id)
        action = data.replace("templateshift:", "")
        current_offset = settings.get('preview_template_offset_y', 0)

        if action == "reset":
            settings['preview_template_offset_y'] = 0
            await query.answer("Template shift reset qilindi")
        elif action.startswith("adjust:"):
            delta = int(action.split(":")[1])
            settings['preview_template_offset_y'] = current_offset + delta
            await query.answer(f"Template shift: {settings['preview_template_offset_y']:+d}px")

        await show_template_shift_settings(query, settings)
        return

    if data.startswith("topmargin:"):
        settings = get_user_settings(context, user_id)
        action = data.replace("topmargin:", "")
        default_top_margin = DEFAULT_SETTINGS.get('margin_top', 240)

        if action == "reset":
            settings['margin_top'] = default_top_margin
            await query.answer("Top margin reset")
        elif action.startswith("adjust:"):
            delta = int(action.split(":")[1])
            settings['margin_top'] = max(0, settings.get('margin_top', default_top_margin) + delta)
            await query.answer(f"Top margin: {settings['margin_top']}px")

        await show_top_margin_settings(query, settings)
        return

    if data.startswith("linespacing:"):
        settings = get_user_settings(context, user_id)
        action = data.replace("linespacing:", "")
        default_line_spacing_adjust = DEFAULT_SETTINGS.get('line_spacing_adjust_px', 0.0)

        if action == "reset":
            settings['line_spacing_adjust_px'] = default_line_spacing_adjust
            await query.answer("Line spacing reset")
        elif action.startswith("adjust:"):
            delta = float(action.split(":")[1])
            current_value = float(settings.get('line_spacing_adjust_px', default_line_spacing_adjust))
            new_value = round(current_value + delta, 2)
            settings['line_spacing_adjust_px'] = new_value
            effective_line_spacing = get_effective_line_spacing_px(settings)
            if effective_line_spacing is not None:
                settings['line_height_multiplier'] = round(effective_line_spacing / settings.get('font_size', 56), 2)
                await query.answer(f"Line spacing: {effective_line_spacing:g}px")
            else:
                await query.answer(f"Line spacing adjustment: {new_value:+g}px")

        await show_line_spacing_settings(query, settings)
        return
    
    # Sozlama qiymatini o'zgartirish
    if data.startswith("setval:"):
        parts = data.split(":")
        setting_type = parts[1]
        value = parts[2]
        
        if setting_type == "format":
            update_user_setting(context, user_id, 'output_format', value)
        elif setting_type == "pagesize":
            update_user_setting(context, user_id, 'page_size', value)
        elif setting_type == "dpi":
            update_user_setting(context, user_id, 'dpi', int(value))
        elif setting_type == "font":
            update_user_setting(context, user_id, 'font_id', int(value))
        elif setting_type == "fontsize":
            update_user_setting(context, user_id, 'font_size', int(value))
        elif setting_type == "previewlayout":
            update_user_setting(context, user_id, 'preview_layout', value)
            if value == 'plain':
                update_user_setting(context, user_id, 'page_size', 'a4')
        elif setting_type == "color":
            update_user_setting(context, user_id, 'text_color', [int(part) for part in value.split(",")])
        elif setting_type == "margin":
            margin = int(value)
            update_user_setting(context, user_id, 'margin_left', margin)
            update_user_setting(context, user_id, 'margin_right', margin)
            update_user_setting(context, user_id, 'margin_top', margin)
            update_user_setting(context, user_id, 'margin_bottom', margin)
        elif setting_type == "lineheight":
            update_user_setting(context, user_id, 'line_height_multiplier', float(value))
        elif setting_type == "paragraphspacing":
            update_user_setting(context, user_id, 'line_break_spacing', int(value))
        elif setting_type == "alignment":
            update_user_setting(context, user_id, 'text_alignment', value)
        
        await query.answer("Saved!")
        settings = get_user_settings(context, user_id)
        if setting_type in ("margin", "lineheight", "paragraphspacing"):
            await show_margin_spacing_settings(query, settings)
        else:
            await show_settings_message(query, settings)
        return
    
    # Effektlarni yoqish/o'chirish
    if data.startswith("toggle:"):
        effect = data.replace("toggle:", "")
        settings = get_user_settings(context, user_id)
        
        if effect == "word_rotation":
            settings['enable_word_rotation'] = not settings['enable_word_rotation']
        elif effect == "natural_variation":
            settings['enable_natural_variation'] = not settings['enable_natural_variation']
        elif effect == "ink_flow":
            settings['enable_ink_flow'] = not settings['enable_ink_flow']
        
        # Effektlar menyusini yangilash
        keyboard = [
            [InlineKeyboardButton(
                f"{'✅' if settings['enable_word_rotation'] else '❌'} Word Rotation",
                callback_data="toggle:word_rotation"
            )],
            [InlineKeyboardButton(
                f"{'✅' if settings['enable_natural_variation'] else '❌'} Natural Variation",
                callback_data="toggle:natural_variation"
            )],
            [InlineKeyboardButton(
                f"{'✅' if settings['enable_ink_flow'] else '❌'} Ink Flow",
                callback_data="toggle:ink_flow"
            )],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        return
    
    # Font testlash
    if data.startswith("testfont:"):
        font_id = int(data.replace("testfont:", ""))
        api_key = os.getenv('HANTEXT_API_KEY')
        if not api_key:
            await query.message.reply_text("The bot is not configured.")
            return
        
        await query.message.chat.send_action("upload_photo")
        
        try:
            # Test matn
            test_text = "Hello! This is a sample of font " + str(font_id) + ".\nHandwriting test: 1234567890"
            
            # Preview yaratish - settings olish va font_id'ni o'zgartirish
            settings = get_user_settings(context, user_id)
            test_settings = settings.copy()
            test_settings['font_id'] = font_id
            test_settings['font_size'] = max(AVAILABLE_FONT_SIZES)
            preview_bytes = text_to_handwritten_preview(test_text, api_key, test_settings)
            
            # Rasmni yuborish
            keyboard = [[InlineKeyboardButton(f"Select font {font_id}", callback_data=f"selectfont:{font_id}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.message.reply_document(
                document=preview_bytes,
                filename=f"font_{font_id}_preview.png",
                caption=f"Font {font_id} sample",
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.error(f"Font preview error: {e}")
            await query.message.reply_text(f"Error: {str(e)}")
        return
    
    # Font tanlash (test dan keyin)
    if data.startswith("selectfont:"):
        font_id = int(data.replace("selectfont:", ""))
        user_id = query.from_user.id
        update_user_setting(context, user_id, 'font_id', font_id)
        await query.message.reply_text(f"Font {font_id} selected!")
        return
    
    # Rang tanlash
    if data.startswith("color:"):
        color_str = data.replace("color:", "")
        try:
            r, g, b = map(int, color_str.split(","))
            user_id = query.from_user.id
            update_user_setting(context, user_id, 'text_color', [r, g, b])
            
            color_name = get_color_name([r, g, b])
            
            await query.message.reply_text(f"Color changed: {color_name}")
        except ValueError:
            await query.message.reply_text("An error occurred.")
        return

    if data.startswith("ps:"):
        cleanup_expired_preview_cache(context)
        parts = data.split(":")
        action = parts[1]
        text_key = parts[2] if len(parts) > 2 else None
        settings = deepcopy(
            context.bot_data.get('text_settings', {}).get(
                text_key,
                get_user_settings(context, user_id)
            )
        )
        raw_preview_bytes = (
            context.bot_data.get('preview_raw_images', {}).get(text_key)
            or context.bot_data.get('preview_images', {}).get(text_key)
        )

        if not raw_preview_bytes:
            await query.message.reply_text(
                "Preview not found. Please send the text again."
            )
            return

        if action == "reset":
            settings['preview_template_offset_y'] = 0
        else:
            settings['preview_template_offset_y'] = settings.get('preview_template_offset_y', 0) + int(action)

        if not context.bot_data.get('text_settings'):
            context.bot_data['text_settings'] = {}
        context.bot_data['text_settings'][text_key] = deepcopy(settings)

        cleaned_raw_preview_bytes = remove_preview_watermark_png_bytes(raw_preview_bytes)
        updated_preview = apply_preview_layout(cleaned_raw_preview_bytes, settings)
        updated_preview_jpg = convert_png_bytes_to_jpg_bytes(updated_preview)

        await query.message.reply_photo(
            photo=updated_preview_jpg,
            caption=(
                "Updated preview\n\n"
                f"Template shift: {settings.get('preview_template_offset_y', 0):+d}px"
            ),
            reply_markup=build_preview_reply_markup(text_key, settings)
        )
        await query.edit_message_text(
            build_preview_shift_text(settings),
            reply_markup=build_preview_shift_reply_markup(text_key)
        )
        return

    if data.startswith("af:"):
        cleanup_expired_preview_cache(context)
        text_key = data.replace("af:", "")
        user_text = context.bot_data.get('texts', {}).get(text_key)
        base_settings = deepcopy(context.bot_data.get('text_settings', {}).get(text_key, get_user_settings(context, user_id)))

        if not user_text:
            await query.message.reply_text(
                "Text not found. Please send it again."
            )
            return

        api_key = os.getenv('HANTEXT_API_KEY')
        if not api_key:
            await query.message.reply_text("The bot is not configured.")
            return

        auto_settings = deepcopy(base_settings)
        auto_settings['font_size'] = 'auto'
        auto_settings['preview_layout'] = 'plain'

        raw_preview_bytes = text_to_handwritten_preview(user_text, api_key, auto_settings)
        clean_auto_preview_bytes = remove_preview_watermark_png_bytes(raw_preview_bytes)
        preview_jpg_bytes = convert_png_bytes_to_jpg_bytes(clean_auto_preview_bytes)
        increment_stat(context, 'auto_fit_preview_count')

        auto_text_key = f"{text_key}_af_{int(time.time())}"
        context.bot_data['texts'][auto_text_key] = user_text
        context.bot_data['text_settings'][auto_text_key] = deepcopy(auto_settings)
        context.bot_data['preview_images'][auto_text_key] = clean_auto_preview_bytes
        context.bot_data['preview_meta'][auto_text_key] = {
            'created_at': time.time(),
            'user_id': user_id
        }

        await query.message.reply_photo(
            photo=preview_jpg_bytes,
            caption="Auto Fit preview\n\nAuto-fit was applied to fit the entire text.",
            reply_markup=build_preview_reply_markup(auto_text_key, auto_settings)
        )
        return

# Error handler
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xatolarni log qilish"""
    logger.error(f"Update {update} caused an error: {context.error}")


class HealthCheckHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for Render/UptimeRobot health checks."""

    def do_GET(self):
        if self.path in ("/", "/health"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = b"not found"
        self.send_response(404)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        """Reduce noisy HTTP access logs in the bot output."""
        return


def start_healthcheck_server():
    """Start a tiny HTTP server so Render and UptimeRobot can check liveness."""
    port = int(os.getenv("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health check server started on port {port}")
    return server


def get_env_bool(name, default=False):
    """Parse a boolean environment variable."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def configure_handlers(application):
    """Register bot handlers on the application instance."""
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("addcredit", admin_add_credit))
    application.add_handler(CommandHandler("balance", show_balance))
    application.add_handler(CommandHandler("buy", buy_menu))
    application.add_handler(CommandHandler("synccommands", sync_commands))
    application.add_handler(CommandHandler(["set", "settings"], settings_menu))
    application.add_handler(CommandHandler("font", set_font))
    application.add_handler(CommandHandler("size", set_size))
    application.add_handler(CommandHandler("fonts", show_fonts))
    application.add_handler(CommandHandler("color", set_color))
    application.add_handler(CommandHandler("aiedit", ai_edit_image))
    application.add_handler(MessageHandler(filters.PHOTO, remember_latest_image))
    application.add_handler(MessageHandler(filters.Document.IMAGE, remember_latest_image))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)


async def run_webhook_mode(token):
    """Run the bot in webhook mode with a custom HTTP app that also exposes /health."""
    webhook_base_url = os.getenv("WEBHOOK_BASE_URL", "").strip().rstrip("/")
    webhook_path = os.getenv("WEBHOOK_PATH", "webhook").strip().strip("/") or "webhook"
    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip() or None
    port = int(os.getenv("PORT", "10000"))

    if not webhook_base_url:
        logger.error("WEBHOOK_BASE_URL is required when USE_WEBHOOK=true")
        print("Please set WEBHOOK_BASE_URL when USE_WEBHOOK=true.")
        return

    webhook_url = f"{webhook_base_url}/{webhook_path}"

    application = (
        Application.builder()
        .token(token)
        .updater(None)
        .read_timeout(60)
        .write_timeout(60)
        .connect_timeout(30)
        .pool_timeout(30)
        .build()
    )
    configure_handlers(application)

    async def health(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def root(_: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def telegram_webhook(request: Request) -> Response:
        if webhook_secret:
            header_secret = request.headers.get("x-telegram-bot-api-secret-token")
            if header_secret != webhook_secret:
                return PlainTextResponse("forbidden", status_code=HTTPStatus.FORBIDDEN)

        await application.update_queue.put(
            Update.de_json(data=await request.json(), bot=application.bot)
        )
        return Response(status_code=HTTPStatus.OK)

    routes = [
        Route("/", root, methods=["GET"]),
        Route("/health", health, methods=["GET"]),
        Route(f"/{webhook_path}", telegram_webhook, methods=["POST"]),
    ]
    web_app = Starlette(routes=routes)

    logger.info(f"Bot starting in webhook mode on port {port} with URL {webhook_url}")
    await application.initialize()
    await application.start()
    await ensure_bot_commands(application)
    await application.bot.set_webhook(
        url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        secret_token=webhook_secret,
    )

    config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        await application.bot.delete_webhook()
        await application.stop()
        await application.shutdown()

def main():
    """Botni ishga tushirish"""
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable was not found!")
        print("Please set TELEGRAM_BOT_TOKEN in the .env file or as an environment variable.")
        return

    use_webhook = get_env_bool("USE_WEBHOOK", False)

    if use_webhook:
        asyncio.run(run_webhook_mode(TOKEN))
    else:
        start_healthcheck_server()
        application = (
            Application.builder()
            .token(TOKEN)
            .post_init(ensure_bot_commands)
            .read_timeout(60)
            .write_timeout(60)
            .connect_timeout(30)
            .pool_timeout(30)
            .build()
        )
        configure_handlers(application)
        logger.info("Bot starting in polling mode")
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False
        )

if __name__ == '__main__':
    main()
