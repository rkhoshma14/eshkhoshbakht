"""
ذخیره‌سازی ساده با SQLite. برای پایداری روی Railway، این مسیر رو
باید روی یک Volume مانت کنی (مثلا /app/data)، وگرنه با هر دیپلوی جدید پاک میشه.
"""
import json
import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS subs (
            user_id INTEGER PRIMARY KEY,
            sub_url TEXT,
            configs TEXT
        )"""
    )
    return conn


def save_sub(user_id: int, sub_url: str, configs: list[str]) -> None:
    conn = _conn()
    conn.execute(
        """INSERT INTO subs (user_id, sub_url, configs) VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET sub_url=excluded.sub_url, configs=excluded.configs""",
        (user_id, sub_url, json.dumps(configs)),
    )
    conn.commit()
    conn.close()


def get_sub(user_id: int):
    conn = _conn()
    row = conn.execute(
        "SELECT sub_url, configs FROM subs WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None, []
    sub_url, configs_json = row
    return sub_url, json.loads(configs_json)
