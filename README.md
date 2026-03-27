# Handwritten Text Telegram Bot 📝✨

Bu Telegram bot text kiritilganda uni handwritten (qo'lda yozilgan) ko'rinishda rasm sifatida qaytaradi.
Bot **HandText AI API** dan foydalanib, yuqori sifatli handwritten rasmlar yaratadi.

## Xususiyatlar

- ✍️ Text ni handwritten ko'rinishga o'zgartiradi
- 🆓 **TEKIN Preview** - Avval ko'ring, keyin to'liq rasm oling!
- 🎨 90 xil handwritten shrift (font_id: 1-90)
- 🤖 HandText AI API integratsiyasi
- 📱 Telegram orqali oson foydalanish
- 🖼️ Yuqori sifatli PNG rasm (shaffof fon)
- ⚡ Tez va professional natija
- 🎯 300 DPI sifatli chiqish
- 💰 API limit tejash - preview tekin!

## O'rnatish

### 1. Repository ni klonlash

```bash
git clone <repository-url>
cd handwritten-telegram-bot
```

### 2. Virtual environment yaratish (ixtiyoriy, lekin tavsiya etiladi)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate
```

### 3. Kerakli paketlarni o'rnatish

```bash
pip install -r requirements.txt
```

### 4. HandText AI API key olish

1. [HandText AI](https://handtextai.com) saytiga o'ting
2. Ro'yxatdan o'ting yoki tizimga kiring
3. Dashboard dan API key ni oling (format: `htext_...`)

### 5. Telegram Bot yaratish

1. Telegram da [@BotFather](https://t.me/BotFather) ni oching
2. `/newbot` komandasini yuboring
3. Bot uchun nom va username o'rnating
4. Token ni saqlang

### 6. Environment variables ni sozlash

`.env` faylini yarating:

```bash
cp .env.example .env
```

`.env` faylini ochib, tokenlar va API key ni kiriting:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
HANTEXT_API_KEY=htext_your_api_key_here
```

> **Eslatma:** HandText AI API key `htext_` bilan boshlanishi kerak.

## Ishga tushirish

```bash
python bot.py
```

Bot ishga tushganidan keyin, Telegram da botingizni oching va `/start` komandasini yuboring.

## Foydalanish

### 📝 Ish Jarayoni:

1. **Text Yuboring** - Botga istalgan textni yuboring
2. **Preview Oling** - Darhol TEKIN preview olasiz (watermark bilan, 1200px)
3. **To'liq Rasm** - Yoqsa "✅ To'liq Sifatli Rasm Olish" tugmasini bosing
4. **Yuklab Oling** - Watermark yo'q, to'liq sifatli PNG rasm olasiz!

### 💰 Narxlar:

- 🆓 **Preview** - TEKIN (cheksiz, watermark bilan, 1200px)
- 💎 **To'liq Rasm** - API limitingizdan hisoblanadi (watermark yo'q, to'liq sifat)

> **Maslahat:** Avval watermarked preview ko'ring, keyin to'liq rasm oling - bu API limitni tejaydi!

## Misol

**1. Sizning textingiz:**
```
Salom, bu handwritten text!
```

**2. Bot javobi:**
```
🎨 Preview (Watermark bilan, 1200px)

Bu watermarked preview - TEKIN! ✅
Watermark yo'q, to'liq sifatli rasm olish uchun pastdagi tugmani bosing! 👇

[✅ To'liq Sifatli Rasm Olish]
```

**3. Tugmani bosilganda:**
```
✅ To'liq sifatli handwritten rasm!

A4 sahifa, 300 DPI. 🎉
```

## Texnologiyalar

- Python 3.8+
- python-telegram-bot 20.7
- requests 2.31.0
- HandText AI API
- python-dotenv 1.0.0

## API Ma'lumotlar

- **Base URL:** `https://api.handtextai.com/api/v1`
- **Endpoints:** 
  - `/preview` - TEKIN watermarked preview (1200px width, aspect ratio saqlanadi)
  - `/generate` - To'liq sifatli rasm (watermark yo'q, API limitdan)
- **Authentication:** Bearer Token (`htext_...`)
- **Font ID Range:** 1-90
- **Dokumentatsiya:** [docs.handtextai.com](https://docs.handtextai.com/introduction)

### Preview vs Generate

| Feature | `/preview` | `/generate` |
|---------|-----------|-------------|
| **Watermark** | ✅ Ha | ❌ Yo'q |
| **Kenglik** | 1200px (auto height) | To'liq sahifa |
| **API Limit** | 🆓 Hisoblanmaydi | 💰 Hisoblanadi |
| **Maqsad** | Test & ko'rish | Final output |

## API Sozlamalari

Bot quyidagi HandText AI API parametrlaridan foydalanadi:

```json
{
  "text": "Sizning textingiz",
  "font_id": 1,
  "dpi": 300,
  "margin_top_px": 100,
  "margin_bottom_px": 100,
  "margin_left_px": 100,
  "margin_right_px": 100,
  "font_size_px": 72,
  "line_spacing_px": 24,
  "text_color": "#000000",
  "background_color": "transparent"
}
```

**Parametrlar:**
- `font_id`: Shrift ID (1-90 oralig'ida)
- `dpi`: DPI sifati (300 yoki 600)
- `margin_*_px`: Margin o'lchamlari (pikselda, 300 DPI asosida)
- `font_size_px`: Font o'lchami (pikselda)
- `line_spacing_px`: Qator orasidagi masofa
- `text_color`: Text rangi (hex kod)
- `background_color`: Fon rangi (`transparent` yoki hex kod)

> **DPI Scaling:** Barcha piksel parametrlari 300 DPI asosida. `dpi: 600` qo'ysangiz, avtomatik 2x scale bo'ladi.

### Font ID larni o'zgartirish

Turli handwritten shriftlardan foydalanish uchun `bot.py` faylida `font_id` ni o'zgartiring (1-90):

```python
# bot.py ichida
image_bytes = text_to_handwritten_image(user_text, api_key, font_id=5)
```

Font ID larni ko'rish uchun: [HandText AI Font Catalog](https://handtextai.com/fonts)

## Muammolarni hal qilish

### API xatolik qaytaradi

Agar bot "API xatolik" degan xabar bersa:
- HandText API key to'g'riligini tekshiring (format: `htext_...`)
- API key faol ekanligini tekshiring
- API limitingiz tugaganligini tekshiring
- Internet aloqangizni tekshiring

**Xatolik kodlari:**
- `401`: API key noto'g'ri yoki yaroqsiz
- `429`: API limit tugadi (keyinroq urinib ko'ring)
- `500`: Server xatolik (HandText AI support bilan bog'laning)

### Bot javob bermayapti

- Internet aloqangizni tekshiring
- Bot tokenining to'g'riligini tekshiring
- `.env` faylida `TELEGRAM_BOT_TOKEN` va `HANTEXT_API_KEY` o'rnatilganini tekshiring
- API limitingiz qolganini tekshiring
- Log fayllarni tekshiring (`python bot.py` ishga tushirganda console loglar ko'rinadi)

## Litsenziya

MIT License

## Muallif

Sizning ismingiz

---

**Eslatma:** Bu bot faqat o'quv maqsadlarida yaratilgan. Production muhitda ishlatishdan oldin xavfsizlik va performance optimizatsiyalarini amalga oshiring.
