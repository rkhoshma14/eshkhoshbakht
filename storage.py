"""
ذخیره‌سازی چند اشتراک برای هر کاربر، با SQLite.
برای پایداری روی Railway، این مسیر رو باید روی یک Volume مانت کنی (مثلا /app/data)،
وگرنه با هر دیپلوی جدید پاک میشه.
"""
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "data/bot.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


NEW_COLUMNS = {"id", "user_id", "name", "note", "sub_url", "configs", "updated_at"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    """جدول‌های نسخه‌ی قدیمی رو به اسکیمای جدید مهاجرت می‌ده."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(subs)").fetchall()}
    if existing:
        if "updated_at" not in existing and {"id", "user_id", "name", "note", "sub_url", "configs"}.issubset(existing):
            conn.execute("ALTER TABLE subs ADD COLUMN updated_at TEXT")
            conn.execute("UPDATE subs SET updated_at = ? WHERE updated_at IS NULL", (_now_iso(),))
            conn.commit()
        elif not NEW_COLUMNS.issubset(existing) and existing:
            conn.execute("ALTER TABLE subs RENAME TO subs_old")
            conn.execute(
                """CREATE TABLE subs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    note TEXT,
                    sub_url TEXT NOT NULL,
                    configs TEXT NOT NULL,
                    updated_at TEXT
                )"""
            )
            if {"user_id", "sub_url", "configs"}.issubset(existing):
                old_rows = conn.execute("SELECT user_id, sub_url, configs FROM subs_old").fetchall()
                now = _now_iso()
                for i, (user_id, sub_url, configs) in enumerate(old_rows, start=1):
                    conn.execute(
                        "INSERT INTO subs (user_id, name, note, sub_url, configs, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, f"اشتراک {i}", "", sub_url, configs, now),
                    )
            conn.execute("DROP TABLE subs_old")
            conn.commit()

    gen_cols = {row[1] for row in conn.execute("PRAGMA table_info(generated_subs)").fetchall()}
    if gen_cols and "expires_at" not in gen_cols:
        conn.execute("ALTER TABLE generated_subs ADD COLUMN expires_at TEXT")
        conn.commit()


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            note TEXT,
            sub_url TEXT NOT NULL,
            configs TEXT NOT NULL,
            updated_at TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS generated_subs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            token TEXT NOT NULL UNIQUE,
            configs TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT
        )"""
    )
    _migrate(conn)
    return conn


def add_sub(user_id: int, name: str, note: str, sub_url: str, configs: list[str]) -> int:
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO subs (user_id, name, note, sub_url, configs, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, note, sub_url, json.dumps(configs), _now_iso()),
    )
    conn.commit()
    sub_id = cur.lastrowid
    conn.close()
    return sub_id


def list_subs(user_id: int) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, name, note, sub_url, configs, updated_at FROM subs WHERE user_id=? ORDER BY id",
        (user_id,),
    ).fetchall()
    conn.close()
    result = []
    for sub_id, name, note, sub_url, configs_json, updated_at in rows:
        configs = json.loads(configs_json)
        result.append(
            {
                "id": sub_id,
                "name": name,
                "note": note or "",
                "sub_url": sub_url,
                "config_count": len(configs),
                "updated_at": updated_at or "",
            }
        )
    return result


def get_sub(sub_id: int, user_id: int) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, name, note, sub_url, configs, updated_at FROM subs WHERE id=? AND user_id=?",
        (sub_id, user_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    sid, name, note, sub_url, configs_json, updated_at = row
    return {
        "id": sid,
        "name": name,
        "note": note or "",
        "sub_url": sub_url,
        "configs": json.loads(configs_json),
        "updated_at": updated_at or "",
    }


def update_configs(sub_id: int, user_id: int, configs: list[str]) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE subs SET configs=?, updated_at=? WHERE id=? AND user_id=?",
        (json.dumps(configs), _now_iso(), sub_id, user_id),
    )
    conn.commit()
    conn.close()


def update_note(sub_id: int, user_id: int, note: str) -> None:
    conn = _conn()
    conn.execute(
        "UPDATE subs SET note=? WHERE id=? AND user_id=?",
        (note, sub_id, user_id),
    )
    conn.commit()
    conn.close()


def delete_sub(sub_id: int, user_id: int) -> None:
    conn = _conn()
    conn.execute("DELETE FROM subs WHERE id=? AND user_id=?", (sub_id, user_id))
    conn.commit()
    conn.close()


# ---------- اشتراک‌های سفارشی ساخته‌شده ----------

def create_generated_sub(
    user_id: int, name: str, configs: list[str], expires_at: str | None = None
) -> tuple[int, str]:
    """یک اشتراک سفارشی جدید می‌سازه و (id, token) برمی‌گردونه."""
    token = secrets.token_urlsafe(12)
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO generated_subs (user_id, name, token, configs, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, name, token, json.dumps(configs), _now_iso(), expires_at),
    )
    conn.commit()
    gen_id = cur.lastrowid
    conn.close()
    return gen_id, token


def _row_to_generated(row) -> dict:
    gid, user_id, name, tok, configs_json, created_at, expires_at = row
    return {
        "id": gid,
        "user_id": user_id,
        "name": name,
        "token": tok,
        "configs": json.loads(configs_json),
        "created_at": created_at,
        "expires_at": expires_at,
    }


def get_generated_by_token(token: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, user_id, name, token, configs, created_at, expires_at FROM generated_subs WHERE token=?",
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_generated(row)


def get_generated_by_id(gen_id: int, user_id: int) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, user_id, name, token, configs, created_at, expires_at FROM generated_subs WHERE id=? AND user_id=?",
        (gen_id, user_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_generated(row)


def list_generated_subs(user_id: int) -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT id, name, token, configs, created_at, expires_at FROM generated_subs WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    result = []
    for gid, name, token, configs_json, created_at, expires_at in rows:
        configs = json.loads(configs_json)
        result.append(
            {
                "id": gid,
                "name": name,
                "token": token,
                "config_count": len(configs),
                "created_at": created_at,
                "expires_at": expires_at,
            }
        )
    return result


def is_generated_expired(gen: dict) -> bool:
    exp = gen.get("expires_at")
    if not exp:
        return False
    try:
        return datetime.fromisoformat(exp) < datetime.now(timezone.utc)
    except Exception:
        return False


def delete_generated_sub(gen_id: int, user_id: int) -> bool:
    conn = _conn()
    cur = conn.execute("DELETE FROM generated_subs WHERE id=? AND user_id=?", (gen_id, user_id))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
