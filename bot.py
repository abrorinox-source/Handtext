import os
from copy import deepcopy
import time
import threading
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
import requests
import base64
import logging
from dotenv import load_dotenv
from PIL import Image, ImageDraw
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# .env faylini yuklash
load_dotenv()

# Logging sozlamalari
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

PREVIEW_CACHE_TTL_SECONDS = 180
AVAILABLE_FONT_SIZES = (56, 64, 72, 80, 88, 96, 104, 112)

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
    'line_break_spacing': 80,
    'enable_word_rotation': False,
    'word_rotation_range': 5.0,
    'enable_natural_variation': False,
    'natural_variation_alpha': 15,
    'natural_variation_sigma': 5,
    'enable_ink_flow': False,
    'ink_flow_intensity': 1.0
}

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
    'plain': 'Plain',
    'school_graph': 'Math Notebook',
    'ona_tili': 'Language Notebook'
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
    80: 62
}

ONA_TILI_FONT_LINE_MAP = {
    56: 73,
    64: 73,
    72: 72.5,
    80: 72
}

SCHOOL_GRAPH_TOP_MARGIN_BY_SIZE = {
    72: -10,
    80: -10
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

def increment_stat(context, key):
    """Oddiy hisoblagichni oshiradi"""
    if not context.bot_data.get('stats'):
        context.bot_data['stats'] = {}
    context.bot_data['stats'][key] = context.bot_data['stats'].get(key, 0) + 1

def is_final_render_enabled(context):
    """Final render yoqilgan yoki o'chirilganini qaytaradi"""
    return context.bot_data.get('final_render_enabled', False)

def set_final_render_enabled(context, enabled):
    """Final render holatini saqlaydi"""
    context.bot_data['final_render_enabled'] = enabled

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
    if layout == 'ona_tili':
        return ONA_TILI_FONT_LINE_MAP.get(font_size, 73)
    if layout == 'school_graph':
        return SCHOOL_GRAPH_FONT_LINE_MAP.get(font_size, 63)
    return None

def get_effective_paragraph_spacing(settings):
    """Paragraph spacing qiymatini qaytaradi"""
    layout = settings.get('preview_layout', 'plain')
    configured_spacing = settings.get('line_break_spacing', 80)

    if is_notebook_preview_layout(layout):
        line_spacing_px = get_effective_line_spacing_px(settings)
        if line_spacing_px is not None:
            return line_spacing_px

    return configured_spacing

def get_color_name(color):
    """Rang nomini qaytaradi"""
    return COLOR_NAMES.get(str(color), f'RGB{color}')

def get_preview_layout_name(layout):
    """Preview layout nomini qaytaradi"""
    return PREVIEW_LAYOUT_NAMES.get(layout, layout)

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
    return layout in ('school_graph', 'ona_tili')

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
        if layout == 'ona_tili':
            line_spacing_px = ONA_TILI_FONT_LINE_MAP.get(font_size, 63)
        else:
            line_spacing_px = SCHOOL_GRAPH_FONT_LINE_MAP.get(font_size, 63)
        top_margin_offset = get_effective_font_top_margin_offset(settings, font_id, font_size)
        top_margin = metrics['top_margin'] + top_margin_offset
        preview_settings['page_size'] = 'a5'
        preview_settings['font_size'] = font_size
        if layout == 'ona_tili':
            preview_settings['margin_left'] = metrics['text_margin'] + ONA_TILI_TEXT_MARGIN_OFFSET_X
            preview_settings['margin_right'] = metrics['base_step'] + ONA_TILI_RIGHT_MARGIN_OFFSET_X
        else:
            preview_settings['margin_left'] = metrics['text_margin']
            preview_settings['margin_right'] = metrics['base_step']
        preview_settings['margin_top'] = top_margin
        preview_settings['margin_bottom'] = max(12, round(metrics['base_step'] * 0.5))
        preview_settings['line_height_multiplier'] = round(line_spacing_px / font_size, 2)

    return preview_settings

def get_effective_generate_settings(settings):
    """Final generate uchun previewga maksimal yaqin effective settingsni qaytaradi"""
    generate_settings = settings.copy()

    if is_notebook_preview_layout(settings.get('preview_layout', 'plain')):
        generate_settings = get_effective_preview_settings(settings)
        generate_settings['output_format'] = settings.get('output_format', 'png')
        generate_settings['dpi'] = settings.get('dpi', 600)

    return generate_settings

def build_school_graph_background(size, offset_y=0):
    """Katakli maktab daftariga o'xshash fon yaratadi"""
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

    draw.line((red_margin, 0, red_margin, height), fill=margin_color, width=2)
    return image

def build_ona_tili_background(size, offset_y=0):
    """Ona tili daftari uslubidagi fon yaratadi"""
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

    draw.line(
        (red_margin + ONA_TILI_RED_MARGIN_OFFSET_X, 0, red_margin + ONA_TILI_RED_MARGIN_OFFSET_X, height),
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
    elif layout == 'ona_tili':
        background = build_ona_tili_background(
            foreground.size,
            settings.get('preview_template_offset_y', 0)
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
        [InlineKeyboardButton("Get Full-Quality Result", callback_data=f"fullselect:{text_key}")]
    ]

    if settings.get('preview_layout') == 'plain':
        auto_fit_label = "Auto Fit enabled" if settings.get('font_size') == 'auto' else "Auto Fit"
        auto_fit_callback = "af:noop" if settings.get('font_size') == 'auto' else f"af:{text_key}"
        keyboard.append([InlineKeyboardButton(auto_fit_label, callback_data=auto_fit_callback)])

    if is_notebook_preview_layout(settings.get('preview_layout')):
        current_offset = settings.get('preview_template_offset_y', 0)
        keyboard.extend([
            [
                InlineKeyboardButton("-5", callback_data=f"ps:-5:{text_key}"),
                InlineKeyboardButton(f"Shift {current_offset:+d}", callback_data="ps:noop"),
                InlineKeyboardButton("+5", callback_data=f"ps:5:{text_key}")
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
            InlineKeyboardButton("-5", callback_data=f"ps:-5:{text_key}"),
            InlineKeyboardButton("Reset", callback_data=f"ps:reset:{text_key}"),
            InlineKeyboardButton("+5", callback_data=f"ps:5:{text_key}")
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

def build_final_shift_reply_markup(result_key):
    """Final output uchun template shift keyboardi"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("-5", callback_data=f"fs:-5:{result_key}"),
            InlineKeyboardButton("Reset", callback_data=f"fs:reset:{result_key}"),
            InlineKeyboardButton("+5", callback_data=f"fs:5:{result_key}")
        ],
        [
            InlineKeyboardButton("-1", callback_data=f"fs:-1:{result_key}"),
            InlineKeyboardButton("Offset", callback_data="fs:noop"),
            InlineKeyboardButton("+1", callback_data=f"fs:1:{result_key}")
        ]
    ])

def build_final_shift_text(settings, output_format):
    """Final output shift holatini matn ko'rinishida qaytaradi"""
    format_label = "PDF" if output_format == "layoutpdf" else "PNG"
    return (
        "Final Shift\n\n"
        f"Format: {format_label}\n"
        f"Current offset: {settings.get('preview_template_offset_y', 0):+d}px\n"
        "Negative values move the lines up, positive values move them down."
    )

def convert_png_bytes_to_pdf_bytes(png_bytes):
    """PNG bytes ni PDF bytes ga o'giradi"""
    image = Image.open(BytesIO(png_bytes)).convert("RGBA")
    white_background = Image.new("RGB", image.size, (255, 255, 255))
    white_background.paste(image, mask=image.getchannel("A"))
    output = BytesIO()
    white_background.save(output, format="PDF", resolution=300.0)
    return output.getvalue()

def convert_png_bytes_to_white_png_bytes(png_bytes):
    """Transparent PNG bytes ni oq fonli PNG bytes ga o'giradi"""
    image = Image.open(BytesIO(png_bytes)).convert("RGBA")
    white_background = Image.new("RGB", image.size, (255, 255, 255))
    white_background.paste(image, mask=image.getchannel("A"))
    output = BytesIO()
    white_background.save(output, format="PNG")
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
        preview_meta.pop(text_key, None)
        texts.pop(text_key, None)
        text_settings.pop(text_key, None)

    final_images = context.bot_data.get('final_images', {})
    final_meta = context.bot_data.get('final_meta', {})
    expired_final_keys = [
        result_key for result_key, meta in final_meta.items()
        if now - meta.get('created_at', 0) > PREVIEW_CACHE_TTL_SECONDS
    ]

    for result_key in expired_final_keys:
        final_images.pop(result_key, None)
        final_meta.pop(result_key, None)

def build_settings_keyboard(settings):
    """Asosiy settings keyboardini quradi"""
    color_name = get_color_name(settings['text_color'])

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Font: {settings['font_id']}", callback_data="setting:font")],
        [InlineKeyboardButton(f"Font size: {settings.get('font_size', 56)} px", callback_data="setting:fontsize")],
        [InlineKeyboardButton(f"Preview Layout: {get_preview_layout_name(settings.get('preview_layout', 'plain'))}", callback_data="setting:previewlayout")],
        [InlineKeyboardButton(f"Color: {color_name}", callback_data="setting:color")],
        [InlineKeyboardButton(f"Top margin: {settings.get('margin_top', 240)} px", callback_data="setting:topmargin")],
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
        f"Top margin: {settings.get('margin_top', 240)} px\n"
        f"Text: {settings['text_alignment']}\n"
        f"Line spacing: {settings['line_height_multiplier']}\n"
        f"Paragraph spacing: {get_effective_paragraph_spacing(settings)} px\n\n"
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
            InlineKeyboardButton("-5", callback_data="templateshift:adjust:-5"),
            InlineKeyboardButton("-1", callback_data="templateshift:adjust:-1")
        ],
        [
            InlineKeyboardButton("+1", callback_data="templateshift:adjust:1"),
            InlineKeyboardButton("+5", callback_data="templateshift:adjust:5"),
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
        f"Top margin\n\n"
        f"Current top margin: {current_top_margin}px\n\n"
        f"Smaller values move text upward. Larger values move text downward.\n"
        f"This controls the global margin_top setting.",
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

# HandText AI API orqali TO'LIQ SIFATLI image yaratish (PNG yoki PDF)
def text_to_handwritten_image(text: str, api_key: str, settings: dict):
    """
    HandText AI API orqali text ni handwritten rasm ga o'zgartiradi
    
    Args:
        text: Handwritten ga o'zgartirilishi kerak bo'lgan text
        api_key: HandText AI API kaliti (htext_... format)
        font_id: Font ID (1-90), default: 1
    
    Returns:
        bytes: PNG formatidagi rasm
    """
    # HandText AI API endpoint (to'g'ri)
    api_url = "https://api.handtextai.com/api/v1/generate"
    
    # API ga yuborilishi kerak bo'lgan ma'lumotlar
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # API parametrlari (settings dan)
    effective_settings = get_effective_generate_settings(settings)
    output_format = effective_settings.get('output_format', 'png')
    
    payload = {
        "text": text,
        "font_id": effective_settings.get('font_id', 1),
        "font_size": effective_settings.get('font_size', 56),
        "page_size": effective_settings.get('page_size', 'a4'),
        "dpi": effective_settings.get('dpi', 300),
        "output_format": output_format,
        "text_color": effective_settings.get('text_color', [0, 0, 255]),
        "text_alignment": effective_settings.get('text_alignment', 'left'),
        "margin_left": effective_settings.get('margin_left', 240),
        "margin_right": effective_settings.get('margin_right', 240),
        "margin_top": effective_settings.get('margin_top', 240),
        "margin_bottom": effective_settings.get('margin_bottom', 240),
        "line_height_multiplier": effective_settings.get('line_height_multiplier', 2.0),
        "enable_word_rotation": effective_settings.get('enable_word_rotation', False),
        "word_rotation_range": effective_settings.get('word_rotation_range', 5.0),
        "enable_natural_variation": effective_settings.get('enable_natural_variation', False),
        "natural_variation_alpha": effective_settings.get('natural_variation_alpha', 15),
        "natural_variation_sigma": effective_settings.get('natural_variation_sigma', 5),
        "enable_ink_flow": effective_settings.get('enable_ink_flow', False),
        "ink_flow_intensity": effective_settings.get('ink_flow_intensity', 1.0)
    }

    if has_paragraph_breaks(text):
        payload["line_break_spacing"] = get_effective_paragraph_spacing(effective_settings)
    
    try:
        logger.info("Sending HandText AI API request...")
        response = requests.post(api_url, json=payload, headers=headers, timeout=120)
        
        if response.status_code == 200:
            # PDF yoki PNG ekanligini tekshirish
            if output_format == 'pdf':
                # PDF - binary response
                pdf_bytes = response.content
                logger.info(f"PDF created successfully. Size: {len(pdf_bytes)} bytes")
                return {'type': 'pdf', 'data': pdf_bytes}
            else:
                # PNG - JSON response
                result = response.json()
                image_base64 = result.get('image_base64')
                if not image_base64:
                    raise Exception("image_base64 was not found in the response")
                
                image_bytes = base64.b64decode(image_base64)
                logger.info(f"PNG created successfully. Size: {len(image_bytes)} bytes")
                return {'type': 'png', 'data': image_bytes}
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
    await update.message.reply_text(
        "Hello!\n\n"
        "I turn text into realistic handwritten writing.\n\n"
        "How it works:\n"
        "1. Send me text\n"
        "2. Get a free preview with a watermark\n"
        "3. If you like it, tap the button to get the full-quality result\n\n"
        "There are 90 handwritten fonts available.\n"
        "Blue ink, high quality (300 DPI preview).\n\n"
        "Commands:\n"
        "/font [number] - Choose a font (1-90)\n"
        "/size [56|64|72|80|88|96|104|112] - Choose font size\n"
        "/set - Open settings\n"
        "/color - Choose text color\n"
        "/fonts - Preview fonts"
    )

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
        "/stats - bot statistics\n\n"
        "/final on|off|status - control final rendering\n\n"
        "Set ADMIN_USER_IDS in .env as a comma-separated list."
    )

async def admin_final_control(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin uchun final renderni yoqish/o'chirish"""
    user = update.message.from_user
    register_user_activity(context, user)

    if not is_admin(user.id):
        await update.message.reply_text("This command is for admins only.")
        return

    action = context.args[0].lower() if context.args else "status"

    if action == "on":
        set_final_render_enabled(context, True)
        await update.message.reply_text("Final rendering enabled.")
        return
    if action == "off":
        set_final_render_enabled(context, False)
        await update.message.reply_text("Final rendering disabled.")
        return

    status_text = "enabled" if is_final_render_enabled(context) else "disabled"
    await update.message.reply_text(
        f"Final rendering is currently: {status_text}\n\n"
        "Usage: /final on | /final off | /final status"
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
        f"Final generate count: {stats.get('final_generate_count', 0)}\n"
        f"Auto-fit preview count: {stats.get('auto_fit_preview_count', 0)}\n"
        f"Final rendering: {'enabled' if is_final_render_enabled(context) else 'disabled'}"
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
            "Recommended: Font 1 or 2 (most stable)"
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
            InlineKeyboardButton("Font 5", callback_data="testfont:5"),
            InlineKeyboardButton("Font 10", callback_data="testfont:10"),
            InlineKeyboardButton("Font 15", callback_data="testfont:15")
        ],
        [
            InlineKeyboardButton("Font 20", callback_data="testfont:20"),
            InlineKeyboardButton("Font 25", callback_data="testfont:25"),
            InlineKeyboardButton("Font 30", callback_data="testfont:30")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "Try the fonts!\n\n"
        "Tap a button to see a sample.\n"
        "If you like one, set it with /font [number].\n\n"
        "Recommended: Font 1 or 2 (most stable)",
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
        
        # AVVAL: Preview yaratish
        raw_preview_bytes = text_to_handwritten_preview(user_text, api_key, settings)
        preview_bytes = apply_preview_layout(raw_preview_bytes, settings)
        preview_jpg_bytes = convert_png_bytes_to_jpg_bytes(preview_bytes)
        increment_stat(context, 'preview_count')
        
        # User text ni context ga saqlash (callback uchun)
        # Telegram callback_data maksimum 64 bytes, shuning uchun butun textni saqlamaymiz
        user_id = update.message.from_user.id
        message_id = update.message.message_id
        
        # Unique key yaratish
        text_key = f"{user_id}_{message_id}"
        
        if not context.bot_data.get('texts'):
            context.bot_data['texts'] = {}
        context.bot_data['texts'][text_key] = user_text
        if not context.bot_data.get('text_settings'):
            context.bot_data['text_settings'] = {}
        context.bot_data['text_settings'][text_key] = deepcopy(settings)
        if not context.bot_data.get('preview_images'):
            context.bot_data['preview_images'] = {}
        context.bot_data['preview_images'][text_key] = raw_preview_bytes
        if not context.bot_data.get('preview_meta'):
            context.bot_data['preview_meta'] = {}
        context.bot_data['preview_meta'][text_key] = {
            'created_at': time.time(),
            'user_id': user_id
        }
        
        # Build the inline keyboard for the full-quality result button.
        keyboard = [
            [InlineKeyboardButton("✅ To'liq Sifatli Rasm Olish", callback_data=f"full:{text_key}")]
        ]
        reply_markup = build_preview_reply_markup(text_key, settings)
        
        # Preview rasmni yuborish
        await update.message.reply_photo(
            photo=preview_jpg_bytes,
            caption="Preview (Watermarked, 1200px)\n\n"
                    "This is a free watermarked preview.\n"
                    "If you want the full-quality result without a watermark, use the button below.",
            reply_markup=reply_markup
        )
        return
        await update.message.reply_document(
            document=preview_bytes,
            filename=f"preview_{text_key}.png",
            caption="🎨 Preview (Watermark bilan, 1200px)\n\n"
                    "Bu watermarked preview - TEKIN! ✅\n"
                    "Watermark yo'q, to'liq sifatli rasm olish uchun pastdagi tugmani bosing! 👇",
            reply_markup=reply_markup
        )

        if is_notebook_preview_layout(settings.get('preview_layout')):
            await update.message.reply_text(
                build_preview_shift_text(settings),
                reply_markup=build_preview_shift_reply_markup(text_key)
            )
        
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

    if data == "ps:noop":
        return

    if data == "fs:noop":
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

    if data == "setting:previewlayout":
        keyboard = [
            [InlineKeyboardButton("Plain", callback_data="setting:plainpagesize")],
            [InlineKeyboardButton("Math Notebook", callback_data="setval:previewlayout:school_graph")],
            [InlineKeyboardButton("Language Notebook", callback_data="setval:previewlayout:ona_tili")],
            [InlineKeyboardButton("Back", callback_data="setting:back")]
        ]
        await query.edit_message_text(
            "Choose a preview layout:\n\nThis currently applies to preview only.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data == "setting:plainpagesize":
        await query.edit_message_text(
            "Choose a page size for the plain layout:",
            reply_markup=build_page_size_keyboard("setplainpage")
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

    if data.startswith("setplainpage:"):
        page_size = data.replace("setplainpage:", "")
        update_user_setting(context, user_id, 'preview_layout', 'plain')
        update_user_setting(context, user_id, 'page_size', page_size)
        await query.answer("Plain layout and page size saved!")
        settings = get_user_settings(context, user_id)
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
        settings = get_user_settings(context, user_id)
        raw_preview_bytes = context.bot_data.get('preview_images', {}).get(text_key)

        if not raw_preview_bytes:
            await query.message.reply_text(
                "Preview not found. Please send the text again."
            )
            return

        if action == "reset":
            settings['preview_template_offset_y'] = 0
        else:
            settings['preview_template_offset_y'] = settings.get('preview_template_offset_y', 0) + int(action)

        updated_preview = apply_preview_layout(raw_preview_bytes, settings)
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
        preview_jpg_bytes = convert_png_bytes_to_jpg_bytes(raw_preview_bytes)
        increment_stat(context, 'auto_fit_preview_count')

        auto_text_key = f"{text_key}_af_{int(time.time())}"
        context.bot_data['texts'][auto_text_key] = user_text
        context.bot_data['text_settings'][auto_text_key] = deepcopy(auto_settings)
        context.bot_data['preview_images'][auto_text_key] = raw_preview_bytes
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

    if data.startswith("fs:"):
        cleanup_expired_preview_cache(context)
        _, action, result_key = data.split(":", 2)
        settings = get_user_settings(context, user_id)
        final_payload = context.bot_data.get('final_images', {}).get(result_key)

        if not final_payload:
            await query.message.reply_text(
                "Final output not found. Please generate it again."
            )
            return

        if action == "reset":
            settings['preview_template_offset_y'] = 0
        else:
            settings['preview_template_offset_y'] = settings.get('preview_template_offset_y', 0) + int(action)

        layout_png = apply_preview_layout(final_payload['raw_png'], settings)
        output_format = final_payload['output_format']

        if output_format == "layoutpdf":
            output_bytes = convert_png_bytes_to_pdf_bytes(layout_png)
            await query.message.reply_document(
                document=output_bytes,
                filename=f"handwritten_{final_payload['page_size']}_{final_payload['dpi']}dpi.pdf",
                caption=(
                    "Updated final PDF\n\n"
                    f"Template shift: {settings.get('preview_template_offset_y', 0):+d}px"
                )
            )
        else:
            output_jpg = convert_png_bytes_to_jpg_bytes(layout_png)
            await query.message.reply_photo(
                photo=output_jpg,
                caption=(
                    "Updated final PNG\n\n"
                    f"Template shift: {settings.get('preview_template_offset_y', 0):+d}px"
                )
            )
            await query.message.reply_document(
                document=layout_png,
                filename=f"handwritten_{final_payload['page_size']}_{final_payload['dpi']}dpi.png",
                caption=f"PNG file\n\n{final_payload['page_size'].upper()}, {final_payload['dpi']} DPI"
            )

        await query.edit_message_text(
            build_final_shift_text(settings, output_format),
            reply_markup=build_final_shift_reply_markup(result_key)
        )
        return
    
    if data.startswith("fullselect:"):
        text_key = data.replace("fullselect:", "")
        settings = deepcopy(context.bot_data.get('text_settings', {}).get(text_key, get_user_settings(context, user_id)))
        if settings.get('preview_layout') == 'plain':
            keyboard = [
                [InlineKeyboardButton("PNG", callback_data=f"fullrun:png:{text_key}")],
                [InlineKeyboardButton("PDF", callback_data=f"fullrun:pdf:{text_key}")]
            ]
            caption = "Choose a format:\n\nPNG - white background, plus a raw transparent PNG\nPDF - white background, plus a raw transparent PNG"
        else:
            layout_name = get_preview_layout_name(settings.get('preview_layout'))
            keyboard = [
                [InlineKeyboardButton(f"{layout_name} PNG", callback_data=f"fullrun:layoutpng:{text_key}")],
                [InlineKeyboardButton(f"{layout_name} PDF", callback_data=f"fullrun:layoutpdf:{text_key}")]
            ]
            caption = f"Choose the final format:\n\nPNG - with {layout_name}\nPDF - with {layout_name}"
        await query.edit_message_caption(
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("full:"):
        text_key = data.replace("full:", "")
        settings = deepcopy(context.bot_data.get('text_settings', {}).get(text_key, get_user_settings(context, user_id)))
        if settings.get('preview_layout') == 'plain':
            keyboard = [
                [InlineKeyboardButton("PNG", callback_data=f"fullrun:png:{text_key}")],
                [InlineKeyboardButton("PDF", callback_data=f"fullrun:pdf:{text_key}")]
            ]
            caption = "Choose a format:\n\nPNG - white background, plus a raw transparent PNG\nPDF - white background, plus a raw transparent PNG"
        else:
            layout_name = get_preview_layout_name(settings.get('preview_layout'))
            keyboard = [
                [InlineKeyboardButton(f"{layout_name} PNG", callback_data=f"fullrun:layoutpng:{text_key}")],
                [InlineKeyboardButton(f"{layout_name} PDF", callback_data=f"fullrun:layoutpdf:{text_key}")]
            ]
            caption = f"Choose the final format:\n\nPNG - with {layout_name}\nPDF - with {layout_name}"
        await query.edit_message_caption(
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if data.startswith("fullrun:"):
        cleanup_expired_preview_cache(context)
        if not is_final_render_enabled(context):
            await query.message.reply_text(
                "Final rendering is temporarily disabled. Please try again later."
            )
            return
        _, output_format, text_key = data.split(":", 2)
        user_text = context.bot_data.get('texts', {}).get(text_key)
        
        if not user_text:
            await query.message.reply_text(
                "Sorry, the text was not found. Please send it again."
            )
            return
        
        # API key ni olish
        api_key = os.getenv('HANTEXT_API_KEY')
        if not api_key:
            await query.message.reply_text(
                "The bot is not configured. Please contact the administrator."
            )
            return
        
        # "Sending photo..." ko'rsatish
        await query.message.chat.send_action("upload_photo")
        
        try:
            # Foydalanuvchining sozlamalarini olish
            user_id = query.from_user.id
            settings = deepcopy(context.bot_data.get('text_settings', {}).get(text_key, get_user_settings(context, user_id)))
            settings['dpi'] = 600
            raw_full_png = None

            layout_output = output_format in ("layoutpng", "layoutpdf")
            pdf_output = output_format in ("pdf", "layoutpdf")
            settings['output_format'] = 'png'
            final_settings = get_effective_generate_settings(settings)
            
            # To'liq sifatli rasm yaratish: production uchun har doim bitta raw PNG olamiz.
            result = text_to_handwritten_image(user_text, api_key, settings)
            increment_stat(context, 'final_generate_count')
            raw_full_png = result['data']

            if layout_output:
                layout_png = apply_preview_layout(raw_full_png, settings)
                result_key = f"{user_id}_{text_key}_{int(time.time())}"
                if not context.bot_data.get('final_images'):
                    context.bot_data['final_images'] = {}
                if not context.bot_data.get('final_meta'):
                    context.bot_data['final_meta'] = {}
                context.bot_data['final_images'][result_key] = {
                    'raw_png': raw_full_png,
                    'output_format': output_format,
                    'page_size': final_settings['page_size'],
                    'dpi': final_settings['dpi']
                }
                context.bot_data['final_meta'][result_key] = {
                    'created_at': time.time(),
                    'user_id': user_id
                }
                if pdf_output:
                    result = {'type': 'pdf', 'data': convert_png_bytes_to_pdf_bytes(layout_png)}
                else:
                    result = {'type': 'png', 'data': layout_png}
            elif pdf_output:
                result = {'type': 'pdf', 'data': convert_png_bytes_to_pdf_bytes(raw_full_png)}
            else:
                result = {'type': 'png', 'data': raw_full_png}
            
            # PDF yoki PNG yuborish
            if result['type'] == 'pdf':
                await query.message.reply_document(
                    document=result['data'],
                    filename=f"handwritten_{final_settings['page_size']}_{final_settings['dpi']}dpi.pdf",
                    caption=f"Done! PDF file\n\n{final_settings['page_size'].upper()}, {final_settings['dpi']} DPI"
                )
                if raw_full_png:
                    await query.message.reply_document(
                        document=raw_full_png,
                        filename=f"handwritten_raw_{final_settings['page_size']}_{final_settings['dpi']}dpi.png",
                        caption=f"Raw PNG (without layout)\n\n{final_settings['page_size'].upper()}, {final_settings['dpi']} DPI"
                    )
                if layout_output:
                    await query.message.reply_text(
                        build_final_shift_text(settings, output_format),
                        reply_markup=build_final_shift_reply_markup(result_key)
                    )
            else:
                png_output_bytes = result['data']
                png_filename = f"handwritten_{final_settings['page_size']}_{final_settings['dpi']}dpi.png"
                png_caption = f"Done! PNG image\n\n{final_settings['page_size'].upper()}, {final_settings['dpi']} DPI"

                if not layout_output and output_format == 'png':
                    png_output_bytes = convert_png_bytes_to_white_png_bytes(raw_full_png)
                    png_caption = f"Done! White-background PNG\n\n{final_settings['page_size'].upper()}, {final_settings['dpi']} DPI"

                await query.message.reply_document(
                    document=png_output_bytes,
                    filename=png_filename,
                    caption=png_caption
                )
                if not layout_output and raw_full_png:
                    await query.message.reply_document(
                        document=raw_full_png,
                        filename=f"handwritten_raw_{final_settings['page_size']}_{final_settings['dpi']}dpi.png",
                        caption=f"Raw PNG (transparent)\n\n{final_settings['page_size'].upper()}, {final_settings['dpi']} DPI"
                    )
                if layout_output:
                    await query.message.reply_document(
                        document=raw_full_png,
                        filename=f"handwritten_raw_{final_settings['page_size']}_{final_settings['dpi']}dpi.png",
                        caption=f"Raw PNG (without layout)\n\n{final_settings['page_size'].upper()}, {final_settings['dpi']} DPI"
                    )
                    await query.message.reply_text(
                        build_final_shift_text(settings, output_format),
                        reply_markup=build_final_shift_reply_markup(result_key)
                    )
            
        except Exception as e:
            logger.error(f"Error while creating the full-quality image: {e}")
            await query.message.reply_text(
                f"Sorry, an error occurred: {str(e)}\n\n"
                "Please try again."
            )

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

def main():
    """Botni ishga tushirish"""
    # Bot tokenini olish
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable was not found!")
        print("Please set TELEGRAM_BOT_TOKEN in the .env file or as an environment variable.")
        return
    
    start_healthcheck_server()

    # Application yaratish
    application = Application.builder().token(TOKEN).build()
    
    # Handlerlarni qo'shish
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_menu))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CommandHandler("final", admin_final_control))
    application.add_handler(CommandHandler(["set", "settings"], settings_menu))
    application.add_handler(CommandHandler("font", set_font))
    application.add_handler(CommandHandler("size", set_size))
    application.add_handler(CommandHandler("fonts", show_fonts))
    application.add_handler(CommandHandler("color", set_color))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))  # Tugma bosilganda
    application.add_error_handler(error_handler)
    
    # Botni ishga tushirish
    logger.info("Bot ishga tushdi!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
