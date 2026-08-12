"""
REST API برای پنل وب. همه‌ی مسیرها نیازمند سشن معتبرن (نگاه کن به auth.py).
هر endpoint دقیقاً همون کاری رو می‌کنه که معادلش تو ربات تلگرام انجام میده،
و روی همون storage.py مشترک کار می‌کنه (دیتای یکسان بین ربات و پنل).
"""
import os
from datetime import datetime, timedelta, timezone

import httpx
from aiohttp import web

import storage
from config_parser import (
    config_fingerprint,
    decode_subscription,
    encode_subscription,
    get_protocol,
    get_remark,
    rename_config,
)
from pinger import ping_configs

BASE_URL = os.environ.get("BASE_URL", "").rstrip("/")


def _make_url(token: str, request: web.Request) -> str:
    if BASE_URL:
        return f"{BASE_URL}/sub/{token}"
    host = request.headers.get("Host", "")
    return f"https://{host}/sub/{token}" if host else f"/sub/{token}"


async def _fetch_configs(sub_url: str) -> tuple[bool, list[str] | str]:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(sub_url)
            resp.raise_for_status()
            configs = decode_subscription(resp.text)
    except Exception as e:
        return False, f"خطا در دریافت لینک: {e}"
    if not configs:
        return False, "هیچ کانفیگی توی این اشتراک پیدا نشد."
    return True, configs


def _config_summary(idx: int, raw: str) -> dict:
    return {"index": idx, "protocol": get_protocol(raw), "remark": get_remark(raw) or ""}


def _sub_summary(sub: dict) -> dict:
    return {
        "id": sub["id"],
        "name": sub["name"],
        "note": sub.get("note", ""),
        "sub_url": sub["sub_url"],
        "config_count": sub.get("config_count", len(sub.get("configs", []))),
        "updated_at": sub.get("updated_at", ""),
    }


def _sub_detail(sub: dict) -> dict:
    d = _sub_summary(sub)
    d["configs"] = [_config_summary(i, c) for i, c in enumerate(sub["configs"])]
    return d


def _gen_summary(gen: dict, request: web.Request) -> dict:
    return {
        "id": gen["id"],
        "name": gen["name"],
        "config_count": gen.get("config_count", len(gen.get("configs", []))),
        "created_at": gen.get("created_at", ""),
        "expires_at": gen.get("expires_at"),
        "expired": storage.is_generated_expired(gen),
        "url": _make_url(gen["token"], request),
        "live": bool(gen.get("items")),
    }


def _err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _json_body(request: web.Request) -> dict | None:
    try:
        return await request.json()
    except Exception:
        return None


# ---------- اشتراک‌ها ----------

async def api_list_subs(request: web.Request) -> web.Response:
    subs = storage.list_subs(request["user_id"])
    return web.json_response([_sub_summary(s) for s in subs])


async def api_add_sub(request: web.Request) -> web.Response:
    body = await _json_body(request)
    if body is None:
        return _err("بدنه‌ی درخواست نامعتبره.")

    sub_url = (body.get("sub_url") or "").strip()
    name = (body.get("name") or "").strip()
    note = (body.get("note") or "").strip()
    if not sub_url or not name:
        return _err("لینک و اسم اجباری هستن.")

    ok, result = await _fetch_configs(sub_url)
    if not ok:
        return _err(result)

    sub_id = storage.add_sub(request["user_id"], name, note, sub_url, result)
    sub = storage.get_sub(sub_id, request["user_id"])
    return web.json_response(_sub_detail(sub), status=201)


async def api_get_sub(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub:
        return _err("اشتراک پیدا نشد.", 404)
    return web.json_response(_sub_detail(sub))


async def api_refresh_sub(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub:
        return _err("اشتراک پیدا نشد.", 404)

    ok, result = await _fetch_configs(sub["sub_url"])
    if not ok:
        return _err(result)

    storage.update_configs(sub_id, request["user_id"], result)
    sub = storage.get_sub(sub_id, request["user_id"])
    return web.json_response(_sub_detail(sub))


async def api_delete_sub(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    if not storage.get_sub(sub_id, request["user_id"]):
        return _err("اشتراک پیدا نشد.", 404)
    storage.delete_sub(sub_id, request["user_id"])
    return web.json_response({"deleted": True})


async def api_update_note(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub:
        return _err("اشتراک پیدا نشد.", 404)

    body = await _json_body(request)
    if body is None:
        return _err("بدنه‌ی درخواست نامعتبره.")

    if body.get("clear"):
        storage.update_note(sub_id, request["user_id"], "")
    else:
        text = (body.get("note") or "").strip()
        if not text:
            return _err("متن یادداشت خالیه.")
        new_note = f"{sub['note']}\n{text}" if sub["note"] else text
        storage.update_note(sub_id, request["user_id"], new_note)

    sub = storage.get_sub(sub_id, request["user_id"])
    return web.json_response(_sub_detail(sub))


async def api_export_sub(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub:
        return _err("اشتراک پیدا نشد.", 404)
    return web.json_response({"content": encode_subscription(sub["configs"])})


async def api_rename_config(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    idx = int(request.match_info["idx"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub or idx >= len(sub["configs"]):
        return _err("کانفیگ پیدا نشد.", 404)

    body = await _json_body(request)
    if body is None:
        return _err("بدنه‌ی درخواست نامعتبره.")
    new_name = (body.get("name") or "").strip()
    if not new_name:
        return _err("اسم نمی‌تونه خالی باشه.")

    # این رنیم موقتیه (فقط برای گرفتن خروجی)، مثل ربات، روی خودِ اشتراک ذخیره نمیشه؛
    # برای ذخیره‌ی دائمی باید از "ساخت اشتراک سفارشی" استفاده کنی.
    renamed = rename_config(sub["configs"][idx], new_name)
    return web.json_response({"renamed": renamed})


# ---------- پینگ / حذف مرده‌ها ----------

async def api_ping_sub(request: web.Request) -> web.Response:
    sub_id = int(request.match_info["sub_id"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub:
        return _err("اشتراک پیدا نشد.", 404)

    results = await ping_configs(sub["configs"])
    out = []
    for i, raw in enumerate(sub["configs"]):
        out.append(
            {"index": i, "protocol": get_protocol(raw), "remark": get_remark(raw) or "", "ms": results.get(i)}
        )
    out.sort(key=lambda r: (r["ms"] is None, r["ms"] if r["ms"] is not None else 0))
    return web.json_response(out)


async def api_delete_dead(request: web.Request) -> web.Response:
    """بدون بدنه (یا بدون indices): پینگ می‌گیره و لیست مرده‌ها رو برمی‌گردونه (پیش‌نمایش، چیزی حذف نمیشه).
    با {"indices": [...]}: دقیقاً همون ایندکس‌ها رو حذف می‌کنه، بدون پینگ گرفتن دوباره
    (که نتیجه‌ی نمایش‌داده‌شده با نتیجه‌ی حذف‌شده همیشه یکی باشه)."""
    sub_id = int(request.match_info["sub_id"])
    sub = storage.get_sub(sub_id, request["user_id"])
    if not sub:
        return _err("اشتراک پیدا نشد.", 404)

    body = await _json_body(request) or {}

    if "indices" in body:
        try:
            dead_indices = {int(i) for i in body["indices"]}
        except (TypeError, ValueError):
            return _err("indices نامعتبره.")
        alive = [c for i, c in enumerate(sub["configs"]) if i not in dead_indices]
        storage.update_configs(sub_id, request["user_id"], alive)
        sub = storage.get_sub(sub_id, request["user_id"])
        return web.json_response({"removed": len(dead_indices), **_sub_detail(sub)})

    if not sub["configs"]:
        return web.json_response({"dead": []})

    results = await ping_configs(sub["configs"])
    dead = [i for i, ms in results.items() if ms is None]
    dead_items = [
        {"index": i, "protocol": get_protocol(sub["configs"][i]), "remark": get_remark(sub["configs"][i]) or ""}
        for i in dead
    ]
    return web.json_response({"dead": dead_items})


# ---------- ساخت اشتراک سفارشی (تک یا چند-منبعی، فرقی نداره) ----------

async def api_build_custom(request: web.Request) -> web.Response:
    body = await _json_body(request)
    if body is None:
        return _err("بدنه‌ی درخواست نامعتبره.")

    name = (body.get("name") or "").strip()
    items = body.get("items") or []
    try:
        expiry_days = int(body.get("expiry_days") or 0)
    except (TypeError, ValueError):
        return _err("expiry_days نامعتبره.")

    if not name:
        return _err("اسم اشتراک اجباریه.")
    if not isinstance(items, list) or not items:
        return _err("حداقل یک کانفیگ انتخاب کن.")

    user_id = request["user_id"]
    subs_cache: dict[int, dict] = {}
    final_configs = []

    recipe = []
    for item in items:
        try:
            sub_id = int(item["sub_id"])
            idx = int(item["index"])
        except (KeyError, TypeError, ValueError):
            return _err("آیتم انتخابی نامعتبره.")

        sub = subs_cache.get(sub_id)
        if sub is None:
            sub = storage.get_sub(sub_id, user_id)
            if not sub:
                return _err(f"اشتراک با id={sub_id} پیدا نشد.", 404)
            subs_cache[sub_id] = sub

        if idx < 0 or idx >= len(sub["configs"]):
            return _err("ایندکس کانفیگ نامعتبره.")

        src_raw = sub["configs"][idx]
        custom_name = (item.get("name") or "").strip()
        raw = rename_config(src_raw, custom_name) if custom_name else src_raw
        final_configs.append(raw)
        recipe.append({
            "sub_id": sub_id,
            "index": idx,
            "fp": config_fingerprint(src_raw),
            "name": custom_name,
        })

    expires_at = None
    if expiry_days > 0:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expiry_days)).isoformat()

    gen_id, _token = storage.create_generated_sub(
        user_id, name, final_configs, expires_at=expires_at, items=recipe
    )
    gen = storage.get_generated_by_id(gen_id, user_id)
    return web.json_response(_gen_summary(gen, request), status=201)


# ---------- اشتراک‌های سفارشی من ----------

async def api_list_generated(request: web.Request) -> web.Response:
    gens = storage.list_generated_subs(request["user_id"])
    return web.json_response([_gen_summary(g, request) for g in gens])


async def api_get_generated(request: web.Request) -> web.Response:
    gen_id = int(request.match_info["gen_id"])
    gen = storage.get_generated_by_id(gen_id, request["user_id"])
    if not gen:
        return _err("پیدا نشد.", 404)
    live = storage.resolve_generated_configs(gen, persist=True)
    data = _gen_summary(gen, request)
    data["config_count"] = len(live)
    data["configs"] = [_config_summary(i, c) for i, c in enumerate(live)]
    data["live"] = bool(gen.get("items"))
    return web.json_response(data)



async def api_add_to_generated(request: web.Request) -> web.Response:
    """افزودن کانفیگ از اشتراک‌های اصلی به یک اشتراک سفارشی موجود.
    body: { "items": [ {"sub_id": 1, "index": 0, "name": "اختیاری"}, ... ] }
    لینک ساب عوض نمی‌شود.
    """
    gen_id = int(request.match_info["gen_id"])
    user_id = request["user_id"]
    gen = storage.get_generated_by_id(gen_id, user_id)
    if not gen:
        return _err("پیدا نشد.", 404)

    body = await _json_body(request)
    if body is None:
        return _err("بدنه‌ی درخواست نامعتبره.")

    items = body.get("items") or []
    if not isinstance(items, list) or not items:
        return _err("حداقل یک کانفیگ انتخاب کن.")

    subs_cache: dict[int, dict] = {}
    final_configs = []

    recipe = []
    for item in items:
        try:
            sub_id = int(item["sub_id"])
            idx = int(item["index"])
        except (KeyError, TypeError, ValueError):
            return _err("آیتم انتخابی نامعتبره.")

        sub = subs_cache.get(sub_id)
        if sub is None:
            sub = storage.get_sub(sub_id, user_id)
            if not sub:
                return _err(f"اشتراک با id={sub_id} پیدا نشد.", 404)
            subs_cache[sub_id] = sub

        if idx < 0 or idx >= len(sub["configs"]):
            return _err("ایندکس کانفیگ نامعتبره.")

        src_raw = sub["configs"][idx]
        custom_name = (item.get("name") or "").strip()
        raw = rename_config(src_raw, custom_name) if custom_name else src_raw
        final_configs.append(raw)
        recipe.append({
            "sub_id": sub_id,
            "index": idx,
            "fp": config_fingerprint(src_raw),
            "name": custom_name,
        })

    total = storage.add_configs_to_generated(gen_id, user_id, final_configs, new_items=recipe)
    if total is None:
        return _err("پیدا نشد.", 404)

    gen = storage.get_generated_by_id(gen_id, user_id)
    data = _gen_summary(gen, request)
    data["added"] = len(final_configs)
    data["config_count"] = total
    return web.json_response(data)


async def api_delete_generated(request: web.Request) -> web.Response:
    gen_id = int(request.match_info["gen_id"])
    if not storage.get_generated_by_id(gen_id, request["user_id"]):
        return _err("پیدا نشد.", 404)
    storage.delete_generated_sub(gen_id, request["user_id"])
    return web.json_response({"deleted": True})


def add_routes(app: web.Application) -> None:
    app.router.add_get("/api/subs", api_list_subs)
    app.router.add_post("/api/subs", api_add_sub)
    app.router.add_get("/api/subs/{sub_id}", api_get_sub)
    app.router.add_post("/api/subs/{sub_id}/refresh", api_refresh_sub)
    app.router.add_delete("/api/subs/{sub_id}", api_delete_sub)
    app.router.add_post("/api/subs/{sub_id}/note", api_update_note)
    app.router.add_get("/api/subs/{sub_id}/export", api_export_sub)
    app.router.add_post("/api/subs/{sub_id}/configs/{idx}/rename", api_rename_config)
    app.router.add_get("/api/subs/{sub_id}/ping", api_ping_sub)
    app.router.add_post("/api/subs/{sub_id}/delete-dead", api_delete_dead)
    app.router.add_post("/api/build-custom", api_build_custom)
    app.router.add_get("/api/generated", api_list_generated)
    app.router.add_get("/api/generated/{gen_id}", api_get_generated)
    app.router.add_post("/api/generated/{gen_id}/add-configs", api_add_to_generated)
    app.router.add_delete("/api/generated/{gen_id}", api_delete_generated)
