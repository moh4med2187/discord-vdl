import os
import re
import asyncio
import tempfile
import shutil
import json
import base64
import discord
from discord import app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL

# ========== الإعدادات ==========
TOKEN = os.getenv("DISCORD_TOKEN")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))  # ارفعها حسب حد سيرفرك
URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)
SUPPORTED_DOMAINS = ("tiktok.com", "instagram.com", "instagr.am", "ig.me", "twitter.com", "x.com", "t.co")

# كوكيز اختيارية لتيك توك من البيئة
TIKTOK_COOKIES_B64 = os.getenv("TIKTOK_COOKIES_B64", "").strip()
TIKTOK_COOKIES_RAW = os.getenv("TIKTOK_COOKIES", "").strip()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ---------- أدوات نظام ----------
def _which(cmd: str) -> bool:
    from shutil import which
    return which(cmd) is not None

async def _run_cmd(args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")

async def _ffprobe_json(path: str):
    if not _which("ffprobe"):
        return None
    code, out, err = await _run_cmd([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_streams", "-show_format", path
    ])
    if code != 0:
        return None
    try:
        return json.loads(out)
    except Exception:
        return None

def _write_cookies_file_if_any(tmpdir: str) -> str | None:
    """
    يكتب cookies.txt من البيئة (B64 أو RAW) إلى ملف مؤقت ويعيد مساره.
    وإلا يرجّع None.
    """
    if TIKTOK_COOKIES_B64:
        try:
            data = base64.b64decode(TIKTOK_COOKIES_B64).decode("utf-8", errors="ignore")
            path = os.path.join(tmpdir, "cookies.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            return path
        except Exception:
            pass
    if TIKTOK_COOKIES_RAW:
        try:
            path = os.path.join(tmpdir, "cookies.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(TIKTOK_COOKIES_RAW)
            return path
        except Exception:
            pass
    return None

# ========== التحميل + التحويل ==========
async def _download_with_ytdlp(url: str):
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    outtmpl = os.path.join(tmpdir, "%(title).80s_%(id)s.%(ext)s")

    cookies_file = _write_cookies_file_if_any(tmpdir)

    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/best",            # أفضل فيديو + أفضل صوت
        "merge_output_format": "mp4",
        "http_headers": {
            # وكيل متصفح حقيقي لتفادي الحظر
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/127.0.0.0 Safari/537.36"),
            "Referer": "https://www.tiktok.com/",
        },
        "prefer_ffmpeg": True,
        "geo_bypass": True,
        # تلميحات خاصة بتيك توك
        "extractor_args": {
            "tiktok": {
                "app_info": ["auto"],   # يحاول استخدام إعدادات تطبيق رسمية
            }
        },
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    def _run_dl():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fp = ydl.prepare_filename(info)
            if not os.path.exists(fp):
                for name in os.listdir(tmpdir):
                    cand = os.path.join(tmpdir, name)
                    if os.path.isfile(cand):
                        fp = cand
                        break
            title = info.get("title", "video")
            return fp, title

    loop = asyncio.get_running_loop()
    src_fp, title = await loop.run_in_executor(None, _run_dl)

    # لا ffmpeg؟ ارجع الملف كما هو
    if not _which("ffmpeg"):
        return src_fp, "video/mp4", title

    # افحص الميتاداتا
    meta = await _ffprobe_json(src_fp) or {}
    v_stream = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in meta.get("streams", []) if s.get("codec_type") == "audio"), None)
    vcodec = (v_stream or {}).get("codec_name", "").lower()
    acodec = (a_stream or {}).get("codec_name", "").lower()
    fr_str = (v_stream or {}).get("avg_frame_rate") or (v_stream or {}).get("r_frame_rate") or "0/1"
    try:
        num, den = fr_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 0.0
    except Exception:
        fps = 0.0

    need_reencode = False
    if vcodec != "h264" or acodec != "aac":
        need_reencode = True
    if fps < 15 or fps > 61:
        need_reencode = True

    out_fp = os.path.join(os.path.dirname(src_fp), "converted.mp4")

    if not need_reencode:
        # ريمكس + أبعاد زوجية + faststart
        code, out, err = await _run_cmd([
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-i", src_fp,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "copy",
            "-c:a", "copy",
            "-movflags", "+faststart",
            out_fp
        ])
        if code != 0 or not os.path.exists(out_fp) or os.path.getsize(out_fp) == 0:
            need_reencode = True

    if need_reencode:
        # ترميز كامل — يضمن عدم التجمّد + توافق عالي
        code, out, err = await _run_cmd([
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-i", src_fp,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",  # لو بطيء كثير على Railway جرّب 'faster' أو 'ultrafast'
            "-crf", "18",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_fp
        ])
        if code != 0 or not os.path.exists(out_fp) or os.path.getsize(out_fp) == 0:
            return src_fp, "video/mp4", title

    try:
        os.remove(src_fp)
    except Exception:
        pass
    return out_fp, "video/mp4", title

# ========== الواجهة ==========
class URLModal(discord.ui.Modal, title="📥 أدخل رابط المقطع"):
    url_input = discord.ui.TextInput(
        label="رابط الفيديو",
        placeholder="ضع رابط TikTok / Instagram / Twitter …",
        style=discord.TextStyle.short
    )

    def __init__(self, requester: discord.User):
        super().__init__(timeout=180)
        self.requester = requester

    async def on_submit(self, interaction: discord.Interaction):
        url = self.url_input.value.strip()
        if not URL_REGEX.search(url):
            await interaction.response.send_message("❌ الرابط غير صالح.", ephemeral=True)
            return
        await interaction.response.send_message("⏳ جاري التحميل... سيتم الإرسال في الخاص ✅", ephemeral=True)
        asyncio.create_task(self._process(url))

    async def _process(self, url: str):
        filepath = None
        try:
            filepath, mime, title = await _download_with_ytdlp(url)
            size_bytes = os.path.getsize(filepath)
            limit_bytes = MAX_UPLOAD_MB * 1024 * 1024
            dm = self.requester.dm_channel or await self.requester.create_dm()

            if size_bytes <= limit_bytes:
                filename = f"{title}.mp4"
                await dm.send(content=f"📥 **تم تحميل مقطعك بنجاح:**\n{title}", file=discord.File(filepath, filename))
            else:
                await dm.send(f"⚠️ حجم الملف {size_bytes/1024/1024:.1f}MB يتجاوز الحد {MAX_UPLOAD_MB}MB.")
        except Exception as e:
            try:
                dm = self.requester.dm_channel or await self.requester.create_dm()
                await dm.send(f"❌ خطأ أثناء التحميل/التحويل:\n{e}")
            except Exception:
                pass
        finally:
            if filepath and os.path.exists(filepath):
                try: os.remove(filepath)
                except: pass

class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📩 اضغط هنا لتحميل مقطع", style=discord.ButtonStyle.primary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(URLModal(requester=interaction.user))

# ========== أمر التثبيت ==========
@bot.tree.command(name="setup_panel", description="إنشاء لوحة التحميل العامة")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_panel(interaction: discord.Interaction):
    await interaction.response.send_message(
        "✅ تم إنشاء لوحة التحميل. يمكن للجميع استخدامها لتحميل المقاطع (النتيجة ستصل في الخاص):",
        view=PanelView()
    )

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

def main():
    if not TOKEN:
        raise RuntimeError("❌ لم يتم العثور على متغير البيئة DISCORD_TOKEN")
    bot.run(TOKEN)

if __name__ == "__main__":
    main()
