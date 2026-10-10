# -*- coding: utf-8 -*-
"""Video Maker bot.

You send (or forward) videos and songs to the bot -> it stores them.
Every hour it builds a video (nature clip + Persian sentence + your page name
+ song) and sends it to you with a ready-to-copy caption. Each of the video
and the caption has its own 👍 / 👎 buttons; 👎 replaces only that part.
"""
import asyncio
import html
import logging
import re
import shutil
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import config
import storage as db
import texts
import video_maker as maker

log = logging.getLogger("videobot")
PERSIAN = re.compile(r"[\u0600-\u06FF]")


# ------------------------------------------------------------------ helpers
def owner_id():
    if config.OWNER_ID:
        return config.OWNER_ID
    v = db.get_kv("owner_id")
    return int(v) if v else None


def is_owner(update: Update) -> bool:
    oid, user = owner_id(), update.effective_user
    return bool(oid and user and user.id == oid)


def today(offset_days=0) -> str:
    now = datetime.now(timezone.utc) + timedelta(minutes=config.TZ_OFFSET_MINUTES)
    return (now + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def kb(prefix: str, gid: int, state: str = "") -> InlineKeyboardMarkup:
    if state == "ok":
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ تأیید شد", callback_data="noop")]]
        )
    if state == "busy":
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton("⏳ در حال ساخت جایگزین...", callback_data="noop")]]
        )
    what = "ویدیو" if prefix == "v" else "کپشن"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(f"👍 {what} اوکیه", callback_data=f"{prefix}:ok:{gid}"),
                InlineKeyboardButton("👎 به دردم نمی‌خوره", callback_data=f"{prefix}:no:{gid}"),
            ]
        ]
    )


def caption_html(text: str) -> str:
    # <pre> block: one tap on it copies the whole caption
    return f"<pre>{html.escape(text)}</pre>"


def origin_username(msg) -> str:
    fo = getattr(msg, "forward_origin", None)
    chat = getattr(fo, "chat", None) if fo else None
    return (getattr(chat, "username", None) or "").lower()


async def download(bot, file_id, path):
    f = await bot.get_file(file_id)
    await f.download_to_drive(custom_path=path)


# ------------------------------------------------------------ collecting files
async def on_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not is_owner(update):
        return
    batch: Counter = context.bot_data.setdefault("batch", Counter())
    origin = origin_username(msg)

    if msg.video:
        v = msg.video
        if (v.duration or 0) < config.MIN_SOURCE_VIDEO_SECONDS:
            batch["video_short"] += 1
        elif v.file_size and v.file_size > config.MAX_DOWNLOAD_BYTES:
            batch["too_big"] += 1
        elif db.add_media("video", v.file_id, v.file_unique_id, v.duration):
            batch["video_ok"] += 1
        else:
            batch["dup"] += 1

    elif msg.audio or msg.voice:
        a = msg.audio or msg.voice
        is_voice = bool(msg.voice)
        title = ""
        if msg.audio:
            title = " ".join(
                x for x in (msg.audio.title, msg.audio.performer, msg.audio.file_name) if x
            )
        if (a.duration or 0) < 20:
            batch["audio_short"] += 1
        elif a.file_size and a.file_size > config.MAX_DOWNLOAD_BYTES:
            batch["too_big"] += 1
        elif origin in config.MIXED_CHANNELS and not PERSIAN.search(title):
            batch["audio_filtered"] += 1
        elif db.add_media("audio", a.file_id, a.file_unique_id, a.duration, is_voice, title):
            batch["audio_ok"] += 1
        else:
            batch["dup"] += 1

    # one summary message after a burst of files, not one reply per file
    for j in context.job_queue.get_jobs_by_name("summary"):
        j.schedule_removal()
    context.job_queue.run_once(send_summary, 4, name="summary", chat_id=msg.chat_id)


async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    batch: Counter = context.bot_data.get("batch") or Counter()
    if not batch:
        return
    lines = []
    if batch["video_ok"] or batch["audio_ok"]:
        lines.append(f"✅ ذخیره شد: {batch['video_ok']} ویدیو، {batch['audio_ok']} آهنگ")
    if batch["video_short"]:
        lines.append(f"⏭️ {batch['video_short']} ویدیو کمتر از {config.MIN_SOURCE_VIDEO_SECONDS} ثانیه بود و رد شد")
    if batch["audio_short"]:
        lines.append(f"⏭️ {batch['audio_short']} فایل صوتی خیلی کوتاه بود و رد شد")
    if batch["audio_filtered"]:
        lines.append(f"🇮🇷 {batch['audio_filtered']} آهنگ اسم فارسی نداشت و رد شد")
    if batch["too_big"]:
        lines.append(f"📦 {batch['too_big']} فایل بیشتر از ۲۰ مگابایت بود و رد شد")
    if batch["dup"]:
        lines.append(f"♻️ {batch['dup']} فایل تکراری بود")
    lines.append(f"📊 مجموع الان: {db.count('video')} ویدیو و {db.count('audio')} آهنگ")
    batch.clear()
    await context.bot.send_message(context.job.chat_id, "\n".join(lines))


# ------------------------------------------------------------ making videos
async def build_new_video(context: ContextTypes.DEFAULT_TYPE):
    """Picks a video + song, renders. Caller must delete result.work when done."""
    bot = context.bot
    async with context.application.bot_data["gen_lock"]:
        for _ in range(8):
            v = db.pick_video(config.MIN_SOURCE_VIDEO_SECONDS)
            if not v:
                raise RuntimeError("no_video")
            a = db.pick_audio_for(v["id"])
            if not a:
                raise RuntimeError("no_audio")

            work = config.TMP_DIR / uuid.uuid4().hex
            work.mkdir(parents=True, exist_ok=True)
            vp, ap = work / "v.mp4", work / "a.bin"
            try:
                await download(bot, v["file_id"], vp)
            except Exception as e:  # file no longer available
                log.warning("video download failed: %s", e)
                db.ban_media(v["id"])
                shutil.rmtree(work, ignore_errors=True)
                continue
            try:
                await download(bot, a["file_id"], ap)
            except Exception as e:
                log.warning("audio download failed: %s", e)
                db.ban_media(a["id"])
                shutil.rmtree(work, ignore_errors=True)
                continue

            sentence = texts.unique_sentence(db)
            try:
                out, dur = await asyncio.to_thread(
                    maker.render, vp, ap, work, sentence,
                    v["duration"], a["duration"], bool(a["is_voice"]),
                )
            except maker.TooShort:
                db.ban_media(a["id"])
                shutil.rmtree(work, ignore_errors=True)
                continue
            except Exception:
                shutil.rmtree(work, ignore_errors=True)
                raise

            db.use_media(v["id"])
            db.use_media(a["id"])
            return SimpleNamespace(work=work, out=out, dur=dur, v=v, a=a, sentence=sentence)
    raise RuntimeError("no_pair")


def explain(err: Exception) -> str:
    s = str(err)
    if s == "no_video":
        return "هنوز ویدیویی ندارم 🎬 چند ویدیوی طبیعت (حداقل ۳۰ ثانیه) برام بفرست."
    if s == "no_audio":
        return "آهنگی که بشه ازش استفاده کرد ندارم 🎶 چند آهنگ یا ویس برام بفرست."
    if s == "no_pair":
        return "ترکیب مناسبی پیدا نشد. چند ویدیو و آهنگ جدید برام بفرست."
    if "فونت" in s:
        return "⚠️ فایل فونت Vazirmatn توی پوشه‌ی fonts نیست."
    return f"⚠️ ساخت ویدیو خطا داد:\n{s[:300]}"


async def create_item(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    r = await build_new_video(context)
    try:
        caption = texts.make_caption(config.BRAND)
        gid = db.create_item(today(), r.v["id"], r.a["id"], r.sentence, caption)
        with open(r.out, "rb") as f:
            await context.bot.send_video(
                chat_id, f, reply_markup=kb("v", gid), supports_streaming=True,
                width=1080, height=1920, duration=int(r.dur), write_timeout=300,
            )
        await context.bot.send_message(
            chat_id, caption_html(caption), parse_mode=ParseMode.HTML,
            reply_markup=kb("c", gid),
        )
    finally:
        shutil.rmtree(r.work, ignore_errors=True)


async def tick(context: ContextTypes.DEFAULT_TYPE):
    oid = owner_id()
    if not oid:
        return
    if db.items_on_day(today()) >= config.DAILY_TARGET:
        return
    if db.pending_count(today(-1)) >= config.MAX_PENDING:
        return
    if db.count("video") == 0 or db.count("audio") == 0:
        return
    try:
        await create_item(context, oid)
    except Exception as e:
        log.exception("scheduled build failed")
        if str(e) not in ("no_video", "no_audio", "no_pair"):
            await context.bot.send_message(oid, explain(e))


# ----------------------------------------------------------------- commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not owner_id() and user:
        db.set_kv("owner_id", user.id)
        log.info("owner set to %s", user.id)
    if not is_owner(update):
        return
    await update.message.reply_text(
        "سلام! 🎬 من ربات ساخت ویدیو هستم.\n\n"
        "ویدیوهای طبیعت (حداقل ۳۰ ثانیه) و آهنگ یا ویس برام بفرست یا فوروارد کن، "
        "من خودم ذخیره‌شون می‌کنم.\n"
        f"هر ساعت یه ویدیو با کپشن آماده برات می‌فرستم (روزی {config.DAILY_TARGET} تا).\n\n"
        "/new ← همین الان یه ویدیو بساز\n"
        "/stats ← آمار"
    )


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    note = await update.message.reply_text("⏳ دارم یه ویدیو می‌سازم، چند لحظه صبر کن...")
    try:
        await create_item(context, update.effective_chat.id)
        await note.delete()
    except Exception as e:
        log.exception("manual build failed")
        await note.edit_text(explain(e))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        f"🎬 ویدیوهای آماده: {db.count('video')}\n"
        f"🎶 آهنگ‌های آماده: {db.count('audio')}\n"
        f"📅 ساخته‌شده‌ی امروز: {db.items_on_day(today())} از {config.DAILY_TARGET}\n"
        f"✅ کاملاً تأییدشده: {db.approved_count()}"
    )


# ------------------------------------------------------------------ ratings
async def on_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def on_rate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_owner(update):
        return
    kind, act, gid_s = q.data.split(":")
    gid = int(gid_s)
    item = db.get_item(gid)
    if not item:
        return

    if kind == "v":
        if act == "ok":
            db.update_item(gid, v_state="ok")
            await q.edit_message_reply_markup(reply_markup=kb("v", gid, "ok"))
            return
        # 👎 video: replace only the video, the caption stays
        await q.edit_message_reply_markup(reply_markup=kb("v", gid, "busy"))
        db.dislike_video(item["video_media"], item["audio_media"])
        r = None
        try:
            r = await build_new_video(context)
            with open(r.out, "rb") as f:
                await q.edit_message_media(
                    media=InputMediaVideo(
                        f, supports_streaming=True, width=1080, height=1920,
                        duration=int(r.dur),
                    ),
                    reply_markup=kb("v", gid),
                )
            db.update_item(
                gid, video_media=r.v["id"], audio_media=r.a["id"],
                sentence=r.sentence, v_state="pending",
            )
        except Exception as e:
            log.exception("replace video failed")
            await q.edit_message_reply_markup(reply_markup=kb("v", gid))
            await context.bot.send_message(q.message.chat_id, explain(e))
        finally:
            if r:
                shutil.rmtree(r.work, ignore_errors=True)

    elif kind == "c":
        if act == "ok":
            db.update_item(gid, c_state="ok")
            await q.edit_message_reply_markup(reply_markup=kb("c", gid, "ok"))
            return
        # 👎 caption: replace only the caption, the video stays
        new = texts.make_caption(config.BRAND)
        while new == item["caption"]:
            new = texts.make_caption(config.BRAND)
        db.update_item(gid, caption=new, c_state="pending")
        await q.edit_message_text(
            caption_html(new), parse_mode=ParseMode.HTML, reply_markup=kb("c", gid)
        )


# --------------------------------------------------------------------- main
async def post_init(app: Application):
    app.bot_data["gen_lock"] = asyncio.Lock()


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN is not set")

    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .connect_timeout(30)
        .read_timeout(120)
        .write_timeout(180)
        .build()
    )
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_noop, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(on_rate, pattern=r"^[vc]:(ok|no):\d+$"))
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & (filters.VIDEO | filters.AUDIO | filters.VOICE),
            on_media,
        )
    )
    app.job_queue.run_repeating(tick, interval=config.TICK_SECONDS, first=60)
    # pending updates (files you sent while the bot was off) are kept, not dropped
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
