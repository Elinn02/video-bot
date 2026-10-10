# -*- coding: utf-8 -*-
"""All settings. Most can be overridden with environment variables."""
import os
from pathlib import Path

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Your numeric Telegram user id (get it from @userinfobot).
# If empty, the first person who sends /start becomes the owner.
_owner = os.environ.get("OWNER_ID", "").strip()
OWNER_ID = int(_owner) if _owner.isdigit() else None

# Where the database and temp files live. On Railway, attach a Volume
# and set DATA_DIR=/data so nothing is lost on redeploy.
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
TMP_DIR = DATA_DIR / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"

# Put the Vazirmatn .ttf file inside the "fonts" folder next to this file.
FONT_DIR = Path(__file__).parent / "fonts"

# Name shown at the bottom of every video and inside the caption.
BRAND = os.environ.get("BRAND", "@voiceclip2")

# How many videos per day, and how often the bot checks whether to make one.
DAILY_TARGET = int(os.environ.get("DAILY_TARGET", "10"))
TICK_SECONDS = int(os.environ.get("TICK_SECONDS", "3600"))
# Stop making new videos while this many are still waiting for your 👍/👎.
MAX_PENDING = int(os.environ.get("MAX_PENDING", "4"))

# Source videos shorter than this are rejected (never downloaded).
MIN_SOURCE_VIDEO_SECONDS = int(os.environ.get("MIN_SOURCE_VIDEO_SECONDS", "30"))
# Length of the finished video.
OUT_SECONDS = int(os.environ.get("OUT_SECONDS", "20"))
MIN_OUT_SECONDS = 8
# The song clip starts here (or later, if the song begins with silence).
AUDIO_START_DEFAULT = int(os.environ.get("AUDIO_START_DEFAULT", "10"))

# Telegram bots can only download files up to 20 MB.
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024 - 1024

# For songs forwarded from these (mixed Iranian/foreign) channels, the song is
# kept only if its title/artist/file name contains Persian letters.
MIXED_CHANNELS = {
    c.strip().lower()
    for c in os.environ.get("MIXED_CHANNELS", "Lovmusicam1").split(",")
    if c.strip()
}

# Iran time (UTC+3:30) for the "videos per day" counter.
TZ_OFFSET_MINUTES = 210
