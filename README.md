# Discord Video Downloader Bot (Python 3.11, Railway-ready)

## ملفات المشروع
- `discord_bot_interactive.py` — بوت ديسكورد تفاعلي بأمر `/download`:
  - Dropdown لاختيار القناة
  - Dropdown لاختيار المنصة (TikTok/Instagram/Twitter(X))
  - Modal لإدخال الرابط ثم تنزيل بالفيديو وإرساله

- `requirements.txt` — المتطلبات.

## التشغيل المحلي
```bash
pip install -r requirements.txt
# ويندوز:
set DISCORD_TOKEN=توكن_بوتك && python -u discord_bot_interactive.py
# ماك/لينكس:
DISCORD_TOKEN=توكن_بوتك python -u discord_bot_interactive.py
```

## Railway (خدمة واحدة فقط)
- أنشئ Service جديد (Worker).
- **Start Command**: `python -u discord_bot_interactive.py`
- **Variables**:
  - `DISCORD_TOKEN` — من Discord Developer Portal
  - (اختياري) `MAX_DISCORD_UPLOAD_MB` — افتراضي 24
- ضع متغير: `PYTHON_VERSION=3.11`

## ملاحظات
- بعض الروابط الخاصة/المحمية لن تعمل (يجب أن تكون عامة).
- لو احتجت ffmpeg لروابط معينة، يمكنك تثبيته في بيئة الاستضافة.
