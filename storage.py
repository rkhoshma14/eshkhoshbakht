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
    gen_cols = {row[1] for row in conn.execute("PRAGMA table_info(generated_subs)").fetchall()}
    if gen_cols and "items" not in gen_cols:
        conn.execute("ALTER TABLE generated_subs ADD COLUMN items TEXT")
    gen_cols = {row[1] for row in conn.execute("PRAGMA table_info(generated_subs)").fetchall()}
    if gen_cols and "note" not in gen_cols:
        conn.execute("ALTER TABLE generated_subs ADD COLUMN note TEXT")
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
            expires_at TEXT,
            items TEXT,
            note TEXT
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
    user_id: int,
    name: str,
    configs: list[str],
    expires_at: str | None = None,
    items: list[dict] | None = None,
    note: str = "",
) -> tuple[int, str]:
    """items: لیست منبع برای لایوآپدیت — [{"sub_id", "index", "name", "fp"}, ...]"""
    token = secrets.token_urlsafe(16)
    conn = _conn()
    cur = conn.execute(
        "INSERT INTO generated_subs (user_id, name, token, configs, created_at, expires_at, items, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            name,
            token,
            json.dumps(configs),
            _now_iso(),
            expires_at,
            json.dumps(items) if items is not None else None,
            note or "",
        ),
    )
    conn.commit()
    gen_id = cur.lastrowid
    conn.close()
    return gen_id, token


def _row_to_generated(row) -> dict:
    """پشتیبانی از شکل‌های قدیمی (بدون items / بدون note) و جدید."""
    # ترتیب: id, user_id, name, token, configs, created_at, expires_at, items, note
    n = len(row)
    gid, user_id, name, tok, configs_json, created_at, expires_at = row[:7]
    items_json = row[7] if n >= 8 else None
    note = row[8] if n >= 9 else ""
    items = None
    if items_json:
        try:
            items = json.loads(items_json)
        except Exception:
            items = None
    return {
        "id": gid,
        "user_id": user_id,
        "name": name,
        "token": tok,
        "configs": json.loads(configs_json),
        "created_at": created_at,
        "expires_at": expires_at,
        "items": items,
        "note": note or "",
    }


def get_generated_by_token(token: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, user_id, name, token, configs, created_at, expires_at, items, note FROM generated_subs WHERE token=?",
        (token,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_generated(row)


def get_generated_by_id(gen_id: int, user_id: int) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT id, user_id, name, token, configs, created_at, expires_at, items, note FROM generated_subs WHERE id=? AND user_id=?",
        (gen_id, user_id),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return _row_to_generated(row)


def list_generated_subs(user_id: int) -> list[dict]:
    # اول اشتراک‌های خیلی قدیمیِ منقضی را پاک کن
    cleanup_old_expired_generated()
    conn = _conn()
    rows = conn.execute(
        "SELECT id, name, token, configs, created_at, expires_at, items, note FROM generated_subs WHERE user_id=? ORDER BY id DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        gid, name, token, configs_json, created_at, expires_at = row[:6]
        items_json = row[6] if len(row) > 6 else None
        note = row[7] if len(row) > 7 else ""
        configs = json.loads(configs_json)
        items = None
        if items_json:
            try:
                items = json.loads(items_json)
            except Exception:
                items = None
        result.append(
            {
                "id": gid,
                "name": name,
                "token": token,
                "config_count": len(configs),
                "created_at": created_at,
                "expires_at": expires_at,
                "items": items,
                "note": note or "",
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


def update_generated_expiry(gen_id: int, user_id: int, expires_at: str | None) -> bool:
    conn = _conn()
    cur = conn.execute(
        "UPDATE generated_subs SET expires_at=? WHERE id=? AND user_id=?",
        (expires_at, gen_id, user_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def update_generated_note(gen_id: int, user_id: int, note: str) -> bool:
    conn = _conn()
    cur = conn.execute(
        "UPDATE generated_subs SET note=? WHERE id=? AND user_id=?",
        (note, gen_id, user_id),
    )
    conn.commit()
    ok = cur.rowcount > 0
    conn.close()
    return ok


def cleanup_old_expired_generated(grace_days: int = 7) -> int:
    """
    اشتراک‌های سفارشی که بیش از grace_days از تاریخ انقضایشان گذشته را
    به صورت خودکار از دیتابیس حذف می‌کند. تعداد حذف‌شده را برمی‌گرداند.
    """
    from datetime import timedelta

    conn = _conn()
    rows = conn.execute(
        "SELECT id, expires_at FROM generated_subs WHERE expires_at IS NOT NULL AND expires_at != ''"
    ).fetchall()
    now = datetime.now(timezone.utc)
    to_delete = []
    for gid, exp in rows:
        try:
            exp_dt = datetime.fromisoformat(exp)
            if exp_dt + timedelta(days=grace_days) < now:
                to_delete.append(gid)
        except Exception:
            continue
    if to_delete:
        conn.executemany("DELETE FROM generated_subs WHERE id=?", [(i,) for i in to_delete])
        conn.commit()
    conn.close()
    return len(to_delete)


def add_configs_to_generated(
    gen_id: int,
    user_id: int,
    new_configs: list[str],
    new_items: list[dict] | None = None,
) -> int | None:
    """کانفیگ‌های جدید (+ آیتم‌های منبع) را به اشتراک سفارشی اضافه می‌کند."""
    conn = _conn()
    row = conn.execute(
        "SELECT configs, items FROM generated_subs WHERE id=? AND user_id=?",
        (gen_id, user_id),
    ).fetchone()
    if not row:
        conn.close()
        return None
    existing = json.loads(row[0])
    existing.extend(new_configs)
    items = None
    if row[1]:
        try:
            items = json.loads(row[1])
        except Exception:
            items = []
    if new_items:
        if items is None:
            items = []
        items.extend(new_items)
    conn.execute(
        "UPDATE generated_subs SET configs=?, items=? WHERE id=? AND user_id=?",
        (json.dumps(existing), json.dumps(items) if items is not None else None, gen_id, user_id),
    )
    conn.commit()
    conn.close()
    return len(existing)


def _resolve_one_item(item: dict, user_id: int, subs_cache: dict) -> tuple[str | None, str | None]:
    """یک آیتم منبع را به کانفیگ خام فعلی تبدیل می‌کند.
    خروجی: (raw_config_or_None, new_fingerprint_or_None)
    اولویت تطبیق:
      ۱) اثرانگشت (fp) — دقیق‌ترین
      ۲) ایندکس — اگر fp عوض شده باشد (مثلاً آدرس سرور تغییر کرده)
    اگر name خالی باشد، remark فعلی منبع حفظ می‌شود (لایو).
    اگر name پر باشد، همان اسم سفارشی ادمین اعمال می‌شود.
    """
    from config_parser import config_fingerprint, rename_config

    try:
        sub_id = int(item["sub_id"])
    except (KeyError, TypeError, ValueError):
        return None, None

    sub = subs_cache.get(sub_id)
    if sub is None:
        sub = get_sub(sub_id, user_id)
        if not sub:
            return None, None
        subs_cache[sub_id] = sub

    configs = sub.get("configs") or []
    fp = item.get("fp") or ""
    idx = item.get("index")
    raw = None
    new_fp = None

    # ۱) جستجو با اثرانگشت
    if fp:
        for cand in configs:
            if config_fingerprint(cand) == fp:
                raw = cand
                new_fp = fp
                break

    # ۲) اگر پیدا نشد، از ایندکس استفاده کن (کانفیگ جابه‌جا یا آدرس عوض شده)
    if raw is None and isinstance(idx, int) and 0 <= idx < len(configs):
        raw = configs[idx]
        new_fp = config_fingerprint(raw)

    if raw is None:
        return None, None

    custom_name = (item.get("name") or "").strip()
    if custom_name:
        raw = rename_config(raw, custom_name)
    return raw, new_fp


def resolve_generated_configs(gen: dict, persist: bool = True) -> list[str]:
    """کانفیگ‌های لایو اشتراک سفارشی را از روی منابع فعلی می‌سازد.
    اگر items نباشد، همان snapshot ذخیره‌شده برمی‌گردد.
    اثرانگشت‌های به‌روز شده هم در items ذخیره می‌شوند.
    """
    items = gen.get("items")
    snapshot = list(gen.get("configs") or [])
    if not items:
        return snapshot

    user_id = gen.get("user_id")
    if user_id is None:
        return snapshot

    subs_cache: dict = {}
    resolved: list[str] = []
    items_updated = False
    new_items = list(items)

    for i, item in enumerate(items):
        raw, new_fp = _resolve_one_item(item, user_id, subs_cache)
        if raw is not None:
            resolved.append(raw)
            # اگر اثرانگشت عوض شده، در recipe به‌روز کن تا دفعه بعد بهتر پیدا شود
            if new_fp and new_fp != (item.get("fp") or ""):
                new_items[i] = dict(item)
                new_items[i]["fp"] = new_fp
                items_updated = True
        elif i < len(snapshot):
            # منبع در دسترس نیست — آخرین نسخه ذخیره‌شده
            resolved.append(snapshot[i])

    if persist and resolved and gen.get("id") is not None:
        conn = _conn()
        if items_updated:
            conn.execute(
                "UPDATE generated_subs SET configs=?, items=? WHERE id=?",
                (json.dumps(resolved), json.dumps(new_items), gen["id"]),
            )
            gen["items"] = new_items
        else:
            conn.execute(
                "UPDATE generated_subs SET configs=? WHERE id=?",
                (json.dumps(resolved), gen["id"]),
            )
        conn.commit()
        conn.close()
        gen["configs"] = resolved

    return resolved


def get_source_sub_ids(gen: dict) -> list[int]:
    """لیست یکتای sub_id های منبع برای یک اشتراک سفارشی."""
    items = gen.get("items") or []
    ids = []
    seen = set()
    for it in items:
        try:
            sid = int(it["sub_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if sid not in seen and sid > 0:
            seen.add(sid)
            ids.append(sid)
    return ids



def delete_config_from_generated(gen_id: int, user_id: int, idx: int) -> int | None:
    """حذف یک کانفیگ از اشتراک سفارشی (و آیتم منبع متناظر). تعداد باقی‌مانده یا None."""
    conn = _conn()
    row = conn.execute(
        "SELECT configs, items FROM generated_subs WHERE id=? AND user_id=?",
        (gen_id, user_id),
    ).fetchone()
    if not row:
        conn.close()
        return None
    configs = json.loads(row[0])
    if idx < 0 or idx >= len(configs):
        conn.close()
        return None
    configs.pop(idx)
    items = None
    if row[1]:
        try:
            items = json.loads(row[1])
        except Exception:
            items = None
    if isinstance(items, list) and idx < len(items):
        items.pop(idx)
    conn.execute(
        "UPDATE generated_subs SET configs=?, items=? WHERE id=? AND user_id=?",
        (json.dumps(configs), json.dumps(items) if items is not None else None, gen_id, user_id),
    )
    conn.commit()
    conn.close()
    return len(configs)


def rename_config_in_generated(gen_id: int, user_id: int, idx: int, new_name: str) -> str | None:
    """رنیم یک کانفیگ داخل اشتراک سفارشی. remark جدید یا None در صورت خطا."""
    from config_parser import rename_config, get_remark

    conn = _conn()
    row = conn.execute(
        "SELECT configs, items FROM generated_subs WHERE id=? AND user_id=?",
        (gen_id, user_id),
    ).fetchone()
    if not row:
        conn.close()
        return None
    configs = json.loads(row[0])
    if idx < 0 or idx >= len(configs):
        conn.close()
        return None
    configs[idx] = rename_config(configs[idx], new_name)
    items = None
    if row[1]:
        try:
            items = json.loads(row[1])
        except Exception:
            items = None
    if isinstance(items, list) and idx < len(items) and isinstance(items[idx], dict):
        items[idx]["name"] = new_name
    conn.execute(
        "UPDATE generated_subs SET configs=?, items=? WHERE id=? AND user_id=?",
        (json.dumps(configs), json.dumps(items) if items is not None else None, gen_id, user_id),
    )
    conn.commit()
    conn.close()
    return get_remark(configs[idx]) or new_name
