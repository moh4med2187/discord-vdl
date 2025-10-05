import os
import re
import asyncio
import tempfile
import shutil
import json
import subprocess
import discord
from discord import app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL

# ========== الإعدادات ==========
TOKEN = os.getenv("DISCORD_TOKEN")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)
SUPPORTED_DOMAINS = ("tiktok.com", "instagram.com", "instagr.am", "ig.me", "twitter.com", "x.com", "t.co")

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

# ========== التحميل والتحويل ==========
async def _download_with_ytdlp(url: str):
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    outtmpl = os.path.join(tmpdir, "%(title).80s_%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "format": "bv*+ba/best",            # أفضل فيديو + أفضل صوت
        "merge_output_format": "mp4",
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "prefer_ffmpeg": True,
    }

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

    # لو ما فيه ffmpeg — نرجّع الملف كما هو
    if not _which("ffmpeg"):
        return src_fp, "video/mp4", title

    # استخدم ffprobe لمعرفة الكوديك/الفريم ريت
    meta = await _ffprobe_json(src_fp) or {}
    v_stream = None
    a_stream = None
    for s in meta.get("streams", []):
        if s.get("codec_type") == "video" and v_stream is None:
            v_stream = s
        if s.get("codec_type") == "audio" and a_stream is None:
            a_stream = s

    vcodec = (v_stream or {}).get("codec_name", "")
    acodec = (a_stream or {}).get("codec_name", "")
    # نحاول قراءة معدل الإطارات
    fr_str = (v_stream or {}).get("avg_frame_rate") or (v_stream or {}).get("r_frame_rate") or "0/1"
    try:
        num, den = fr_str.split("/")
        fps = float(num) / float(den) if float(den) != 0 else 0.0
    except Exception:
        fps = 0.0

    # إذا الفيديو H.264 والصوت AAC ومعدل الإطارات قريب من 30 ونمط ألوان yuv420p
    # نكتفي بريمكس + فرض faststart وتطبيع الأبعاد إن لزم.
    need_reencode = False
    if vcodec.lower() != "h264" or acodec.lower() != "aac":
        need_reencode = True
    # لو fps غريب جدًا (0 أو > 61) أو متغيّر — نثبّت 30
    if fps < 15 or fps > 61:
        need_reencode = True

    out_dir = os.path.dirname(src_fp)
    out_fp = os.path.join(out_dir, "converted.mp4")

    if not need_reencode:
        # ريمكس + تأكيد faststart + تأكد الأبعاد زوجية و yuv420p
        # ملاحظة: بعض الملفات تكون yuvj420p أو أبعاد فردية → نجبر فِلتر بسيط.
        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-i", src_fp,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-c:v", "copy",    # سنحاول copy أولاً
            "-c:a", "copy",
            "-movflags", "+faststart",
            out_fp
        ]
        code, out, err = await _run_cmd(cmd)
        # لو فشل copy بسبب عدم توافق الفلتر، نعيد بترميز كامل
        if code != 0 or not os.path.exists(out_fp) or os.path.getsize(out_fp) == 0:
            need_reencode = True

    if need_reencode:
        # ترميز كامل لضمان التوافق + منع التجمّد
        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-i", src_fp,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30",  # أبعاد زوجية + 30fps ثابت
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "18",
            "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",  # مفاتيح إطارات منتظمة
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_fp
        ]
        code, out, err = await _run_cmd(cmd)
        if code != 0 or not os.path.exists(out_fp) or os.path.getsize(out_fp) == 0:
            # لو فشل لأي سبب، رجّع الأصلي بدل ما نفشل
            return src_fp, "video/mp4", title

    # نجاح — نظّف المصدر
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
                await dm.send(
                    content=f"📥 **تم تحميل مقطعك بنجاح:**\n{title}",
                    file=discord.File(filepath, filename)
                )
            else:
                await dm.send(
                    f"⚠️ حجم الملف {size_bytes/1024/1024:.1f}MB يتجاوز الحد {MAX_UPLOAD_MB}MB.\n"
                    "جرّب جودة أقل أو رابط آخر."
                )
        except Exception as e:
            try:
                dm = self.requester.dm_channel or await self.requester.create_dm()
                await dm.send(f"❌ حدث خطأ أثناء التحميل/التحويل:\n{e}")
            except Exception:
                pass
        finally:
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except Exception:
                    pass

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
