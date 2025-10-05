import os
import re
import asyncio
import tempfile
import shutil
import discord
from discord import app_commands
from discord.ext import commands
from yt_dlp import YoutubeDL

TOKEN = os.getenv("DISCORD_TOKEN")
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "100"))
URL_REGEX = re.compile(r"(https?://[^\s]+)", re.IGNORECASE)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

async def run_cmd(args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode(), err.decode()

async def download_video(url):
    tmpdir = tempfile.mkdtemp(prefix="dl_")
    outtmpl = os.path.join(tmpdir, "%(title).80s_%(id)s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
        "format": "bv*+ba/best",
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
        },
    }

    def _run_dl():
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fp = ydl.prepare_filename(info)
            return fp, info.get("title", "video")

    loop = asyncio.get_running_loop()
    src_fp, title = await loop.run_in_executor(None, _run_dl)
    converted_fp = os.path.join(tmpdir, "converted.mp4")

    code, _, err = await run_cmd([
        "ffmpeg", "-y", "-i", src_fp,
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2,fps=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        converted_fp
    ])

    if code != 0 or not os.path.exists(converted_fp):
        print("⚠️ فشل التحويل، سيتم إرسال الملف الأصلي:", err)
        return src_fp, title
    return converted_fp, title

class URLModal(discord.ui.Modal, title="الـصـق رابـط الـمـقـطـع"):
    url_input = discord.ui.TextInput(
        label=" تـأكـد الـحـسـاب مـا يـكـون خـاص",
        placeholder="Tiktok ضـع رابـط  ",
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
        await interaction.response.send_message("سيتم ارسال المقطع لك في الخاص", ephemeral=True)
        asyncio.create_task(self.process(url))

    async def process(self, url: str):
        file_path = None
        try:
            file_path, title = await download_video(url)
            size = os.path.getsize(file_path)
            limit = MAX_UPLOAD_MB * 1024 * 1024
            dm = self.requester.dm_channel or await self.requester.create_dm()

            if size <= limit:
                await dm.send(content=f"📽️ **{title}**", file=discord.File(file_path, f"{title}.mp4"))
            else:
                await dm.send(f"⚠️ المقطع كبير ({size/1024/1024:.1f} MB) ويتجاوز الحد المسموح.")
        except Exception as e:
            try:
                dm = self.requester.dm_channel or await self.requester.create_dm()
                await dm.send(f"❌ خطأ أثناء التحميل:\n{e}")
            except:
                pass
        finally:
            if file_path and os.path.exists(file_path):
                os.remove(file_path)

class PanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="اضـغـط لـتـحـمـيـل مـقـطـع", style=discord.ButtonStyle.secondary)
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(URLModal(requester=interaction.user))

@bot.tree.command(name="setup_panel", description="إنشاء لوحة التحميل العامة")
@app_commands.checks.has_permissions(manage_guild=True)
async def setup_panel(interaction: discord.Interaction):
    await interaction.response.send_message("𝐍𝐗𝐒 𝐕𝐈𝐃𝐄𝐎 𝐃𝐎𝐖𝐍𝐋𝐎𝐀𝐃𝐄𝐑 ╾━╤デ╦︻", view=PanelView())

@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"✅ Logged in as {bot.user}")

if __name__ == "__main__":
    bot.run(TOKEN)