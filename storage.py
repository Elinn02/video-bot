# -*- coding: utf-8 -*-
"""Tiny SQLite storage: pool of videos/songs, generated items, ratings."""
import random
import sqlite3
from datetime import datetime, timezone

import config

_conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
_conn.row_factory = sqlite3.Row

_conn.executescript(
    """
CREATE TABLE IF NOT EXISTS media(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,                 -- 'video' or 'audio'
    file_id TEXT NOT NULL,
    unique_id TEXT NOT NULL UNIQUE,
    duration INTEGER NOT NULL DEFAULT 0,
    is_voice INTEGER NOT NULL DEFAULT 0,
    title TEXT DEFAULT '',
    banned INTEGER NOT NULL DEFAULT 0,
    dislikes INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0,
    added TEXT
);
CREATE TABLE IF NOT EXISTS banned_pairs(
    video_id INTEGER, audio_id INTEGER,
    PRIMARY KEY(video_id, audio_id)
);
CREATE TABLE IF NOT EXISTS items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT,
    video_media INTEGER,
    audio_media INTEGER,
    sentence TEXT,
    caption TEXT,
    v_state TEXT DEFAULT 'pending',
    c_state TEXT DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS used_text(
    kind TEXT, text TEXT, PRIMARY KEY(kind, text)
);
CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT);
"""
)
_conn.commit()


# ---------- key/value ----------
def get_kv(key):
    r = _conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def set_kv(key, value):
    _conn.execute(
        "INSERT INTO kv(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    _conn.commit()


# ---------- media pool ----------
def add_media(kind, file_id, unique_id, duration, is_voice=False, title=""):
    """Returns True if added, False if it was already in the pool."""
    try:
        _conn.execute(
            "INSERT INTO media(kind,file_id,unique_id,duration,is_voice,title,added) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                kind,
                file_id,
                unique_id,
                int(duration or 0),
                1 if is_voice else 0,
                (title or "")[:200],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        _conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def count(kind):
    r = _conn.execute(
        "SELECT COUNT(*) c FROM media WHERE kind=? AND banned=0", (kind,)
    ).fetchone()
    return r["c"]


def _least_used(rows):
    if not rows:
        return None
    low = min(r["uses"] for r in rows)
    return random.choice([r for r in rows if r["uses"] == low])


def pick_video(min_duration=0):
    rows = _conn.execute(
        "SELECT * FROM media WHERE kind='video' AND banned=0 AND duration>=?",
        (min_duration,),
    ).fetchall()
    return _least_used(rows)


def pick_audio_for(video_id, min_duration=15):
    bad = {
        r["audio_id"]
        for r in _conn.execute(
            "SELECT audio_id FROM banned_pairs WHERE video_id=?", (video_id,)
        )
    }
    rows = _conn.execute(
        "SELECT * FROM media WHERE kind='audio' AND banned=0 AND duration>=?",
        (min_duration,),
    ).fetchall()
    rows = [r for r in rows if r["id"] not in bad]
    return _least_used(rows)


def use_media(media_id):
    _conn.execute("UPDATE media SET uses=uses+1 WHERE id=?", (media_id,))
    _conn.commit()


def ban_media(media_id):
    _conn.execute("UPDATE media SET banned=1 WHERE id=?", (media_id,))
    _conn.commit()


def dislike_video(video_id, audio_id):
    """👎 on a video: never use that source video again, never pair that
    video with that song again, and ban the song after 2 dislikes."""
    _conn.execute("UPDATE media SET banned=1 WHERE id=?", (video_id,))
    _conn.execute(
        "INSERT OR IGNORE INTO banned_pairs(video_id,audio_id) VALUES(?,?)",
        (video_id, audio_id),
    )
    _conn.execute("UPDATE media SET dislikes=dislikes+1 WHERE id=?", (audio_id,))
    _conn.execute("UPDATE media SET banned=1 WHERE id=? AND dislikes>=2", (audio_id,))
    _conn.commit()


# ---------- items ----------
def create_item(day, video_media, audio_media, sentence, caption):
    cur = _conn.execute(
        "INSERT INTO items(day,video_media,audio_media,sentence,caption) "
        "VALUES(?,?,?,?,?)",
        (day, video_media, audio_media, sentence, caption),
    )
    _conn.commit()
    return cur.lastrowid


def get_item(item_id):
    return _conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()


_ALLOWED = {"video_media", "audio_media", "sentence", "caption", "v_state", "c_state"}


def update_item(item_id, **fields):
    fields = {k: v for k, v in fields.items() if k in _ALLOWED}
    if not fields:
        return
    sets = ",".join(f"{k}=?" for k in fields)
    _conn.execute(
        f"UPDATE items SET {sets} WHERE id=?", (*fields.values(), item_id)
    )
    _conn.commit()


def items_on_day(day):
    return _conn.execute(
        "SELECT COUNT(*) c FROM items WHERE day=?", (day,)
    ).fetchone()["c"]


def pending_count(since_day):
    return _conn.execute(
        "SELECT COUNT(*) c FROM items WHERE day>=? AND (v_state='pending' OR c_state='pending')",
        (since_day,),
    ).fetchone()["c"]


def approved_count():
    return _conn.execute(
        "SELECT COUNT(*) c FROM items WHERE v_state='ok' AND c_state='ok'"
    ).fetchone()["c"]


# ---------- used texts (avoid repeats) ----------
def text_used(kind, text):
    return (
        _conn.execute(
            "SELECT 1 FROM used_text WHERE kind=? AND text=?", (kind, text)
        ).fetchone()
        is not None
    )


def mark_text(kind, text):
    _conn.execute(
        "INSERT OR IGNORE INTO used_text(kind,text) VALUES(?,?)", (kind, text)
    )
    _conn.commit()
