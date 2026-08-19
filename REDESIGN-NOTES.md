# بازطراحی حرفه‌ای سه‌بعدی — خوشبخت (v3)

الهام‌گرفته از استایل‌های premium در [Lapa Ninja](https://www.lapa.ninja) و [Recent Design](https://recent.design)

## چه چیزی عوض شده؟

### ظاهر (فقط CSS + theme)
- **Glassmorphism عمیق** با blur + saturate
- **افکت سه‌بعدی CSS**: `translateY` + `rotateX` روی کارت‌ها، لیست‌ها و statها
- **سایه‌های لایه‌ای** (depth shadow) مثل داشبوردهای Awwwards
- **نور محیطی حجمی** (radial gradients طلایی/تیل/آبی)
- **noise texture** خیلی ملایم برای حس premium
- **مودال** با انیمیشن ورود سه‌بعدی
- **دکمه‌ها** با inset highlight و glow قوی‌تر
- **hover** نرم و شناور روی همه عناصر تعاملی

### حفظ شده
- تمام کلاس‌های CSS
- تمام منطق `app.js`
- تمام APIها و قابلیت‌ها
- RTL و موبایل

### ربات تلگرام
ربات تلگرام UI خودش را دارد و قابل بازطراحی بصری کامل نیست.
فقط متن دکمه‌ها و پیام‌ها قابل تغییر است (در صورت نیاز بگو).

### مینی‌اپ
همان پنل است — با theme-color و رنگ پس‌زمینه هماهنگ با طراحی جدید.

## فایل‌ها برای جایگزینی
1. `static/style.css`
2. `static/panel.html`
3. `panel_pages.py`

بعد از دیپلوی: Hard Refresh (`Ctrl+Shift+R`)
