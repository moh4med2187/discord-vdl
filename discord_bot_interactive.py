import os
import re
import asyncio
import tempfile
import shutil
import discord
from discord import app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL

# ========== الإعدادات ==========
TOKEN = os.getenv("DISCORD_TOKEN")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))  # أقصى حجم (سيرفر لفل ماكس)
URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)
SUPPORTED_DOMAINS = ("tiktok.com", "instagram.com", "instagr.am", "ig.me", "twitter.com", "x.com", "t.co")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ========== تحميل الفيديو وتحويله بجودة عالية ==========
async def _download_with_ytdlp(url: str):
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    outtmpl = os.path.join(tmpdir, "%(title).80s_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "format": "bv*+ba/best",
        "merge_output_format": "mp4",
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "prefer_ffmpeg": True,
    }

    def _run():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fp = ydl.prepare_filename(info)
            if not os.path.exists(fp):
                for name in os.listdir(tmpdir):
                    cand = os.path.join(tmpdir, name)
                    if os.path.isfile(cand):
                        fp = cand
                        break
            return fp, info.get("title", "video")

    loop = asyncio.get_running_loop()
    src_fp, title = await loop.run_in_executor(None, _run)

    # 🔧 تحويل الفيديو لضمان التوافق والجودة
    if shutil.which("ffmpeg"):
        out_fp = os.path.join(os.path.dirname(src_fp), "converted.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-hwaccel", "auto",
            "-i", src_fp,
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            out_fp
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()

        if proc.returncode == 0 and os.path.exists(out_fp):
            try:
                os.remove(src_fp)
            except:
                pass
            return out_fp, "video/mp4", title
    return src_fp, "video/mp4", title


# ========== واجهة المستخدم ==========
class URLModal(discord.ui.Modal, title=" ألصق رابط المقطع"):
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
        await interaction.response.send_message("سيتم ارسال المقطع لك بالخاص", ephemeral=True)
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
                    f"⚠️ الملف كبير جدًا ({size_bytes/1024/1024:.1f}MB). الحد المسموح {MAX_UPLOAD_MB}MB."
                )
        except Exception as e:
            try:
                dm = self.requester.dm_channel or await self.requester.create_dm()
                await dm.send(f"❌ حدث خطأ أثناء التحميل:\n{e}")
            except:
                pass
        finally:
            if filepath and os.path.exists(filepath):
                try:
                    os.remove(filepath)
                except:
                    pass


class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="اضـغـط لـتـحـمـيـل مـقـطـع", style=discord.ButtonStyle.secondary)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(URLModal(requester=interaction.user))


# ========== أوامر البوت ==========
@bot.tree.command(name="setup_panel", description="إنشاء لوحة التحميل العامة")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_panel(interaction: discord.Interaction):
    await interaction.response.send_message(
        "𝐍𝐗𝐒 𝐕𝐈𝐃𝐄𝐎 𝐃𝐎𝐖𝐍𝐋𝐎𝐀𝐃𝐄𝐑 ╾━╤デ╦︻",
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
