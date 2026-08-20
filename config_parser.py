"""
پارس و رنیم کردن کانفیگ‌های داخل یک لینک اشتراک (subscription).
پشتیبانی از: vmess, vless, trojan, ss (shadowsocks), ssr, hysteria2, tuic
"""
import base64
import json
import re
from urllib.parse import quote, unquote, urlparse


def _b64decode(s: str) -> bytes:
    s = s.strip().replace("-", "+").replace("_", "/")
    padding = "=" * (-len(s) % 4)
    return base64.b64decode(s + padding)


def decode_subscription(content: str) -> list[str]:
    """محتوای body لینک اشتراک (معمولا base64) رو به لیست خطوط کانفیگ خام تبدیل می‌کنه."""
    content = content.strip()
    try:
        decoded = _b64decode(content).decode("utf-8", errors="ignore")
    except Exception:
        decoded = content  # شاید از قبل متن ساده (plain) باشه

    lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    # فیلتر کردن خطوطی که واقعا شبیه یک URI کانفیگ هستن
    lines = [l for l in lines if "://" in l]
    return lines


def encode_subscription(configs: list[str]) -> str:
    """لیست کانفیگ‌ها رو به یک رشته base64 (لینک اشتراک) تبدیل می‌کنه."""
    body = "\n".join(configs)
    return base64.b64encode(body.encode("utf-8")).decode("utf-8")


def get_protocol(raw: str) -> str:
    return raw.split("://", 1)[0] if "://" in raw else "unknown"


def get_remark(raw: str) -> str:
    """اسم فعلی (remark) یک کانفیگ رو استخراج می‌کنه."""
    if raw.startswith("vmess://"):
        try:
            data = json.loads(_b64decode(raw[len("vmess://"):]))
            return data.get("ps", "")
        except Exception:
            return ""
    if "#" in raw:
        try:
            return unquote(raw.split("#", 1)[1])
        except Exception:
            return raw.split("#", 1)[1]
    return ""


def get_host_port(raw: str) -> tuple[str, int] | None:
    """آدرس سرور و پورت رو از یک کانفیگ خام استخراج می‌کنه (برای تست پینگ)."""
    if raw.startswith("vmess://"):
        try:
            data = json.loads(_b64decode(raw[len("vmess://"):]))
            host = data.get("add")
            port = int(data.get("port"))
            if host and port:
                return host, port
        except Exception:
            pass
        return None

    try:
        parsed = urlparse(raw)
        if parsed.hostname and parsed.port:
            return parsed.hostname, parsed.port
    except Exception:
        pass

    # فرمت قدیمی ss:// که همه چیز قبل از # به صورت base64 هست
    if raw.startswith("ss://"):
        try:
            body = raw[len("ss://"):].split("#", 1)[0]
            decoded = _b64decode(body).decode("utf-8", errors="ignore")
            m = re.search(r"@([^:/?#]+):(\d+)", decoded)
            if m:
                return m.group(1), int(m.group(2))
        except Exception:
            pass

    return None


def rename_config(raw: str, new_name: str) -> str:
    """یک کانفیگ خام رو با اسم جدید برمی‌گردونه."""
    if raw.startswith("vmess://"):
        try:
            data = json.loads(_b64decode(raw[len("vmess://"):]))
            data["ps"] = new_name
            new_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            new_b64 = base64.b64encode(new_json.encode("utf-8")).decode("utf-8")
            return f"vmess://{new_b64}"
        except Exception:
            return raw
    else:
        base = raw.split("#", 1)[0]
        return f"{base}#{quote(new_name)}"


def config_fingerprint(raw: str) -> str:
    """شناسه کانفیگ برای همگام‌سازی با منبع.

    host/port/id به‌تنهایی کافی نیست: بعضی ساب‌ها چند نود «اطلاعاتی»
    با آدرس یکسان (مثلاً 127.0.0.1) و فقط remark متفاوت دارند
    (مثل Usage و Active). اگر remark در اثرانگشت نباشد، هر دو به
    یک کانفیگ resolve می‌شوند و اسم‌ها یکی می‌شود.

    remark هم بخشی از اثرانگشت است تا این نودها جدا بمانند.
    اگر منبع remark را عوض کند (مثلاً عدد مصرف)، fp عوض می‌شود
    و resolve با fallback روی index به نسخهٔ جدید می‌رسد.
    """
    proto = get_protocol(raw)
    hp = get_host_port(raw)
    host_port = f"{hp[0]}:{hp[1]}" if hp else ""
    remark = get_remark(raw) or ""
    if raw.startswith("vmess://"):
        try:
            data = json.loads(_b64decode(raw[len("vmess://"):]))
            uid = data.get("id") or ""
            return f"vmess|{host_port}|{uid}|{remark}"
        except Exception:
            pass
    try:
        base = raw.split("#", 1)[0]
        return f"{proto}|{host_port}|{base[-48:]}|{remark}"
    except Exception:
        return f"{proto}|{raw[:64]}|{remark}"


# ---------- کانفیگ فیک نمایش‌دهنده مدت اعتبار ----------

def remaining_time_text(expires_at: str | None) -> str:
    """متن فارسی مدت اعتبار باقی‌مانده (یا وضعیت منقضی / بدون محدودیت)."""
    from datetime import datetime, timezone

    if not expires_at:
        return "♾ بدون محدودیت زمانی"
    try:
        exp_dt = datetime.fromisoformat(expires_at)
        now = datetime.now(timezone.utc)
        if exp_dt <= now:
            return "⛔ اشتراک منقضی شده"
        delta = exp_dt - now
        days = delta.days
        hours = delta.seconds // 3600
        minutes = (delta.seconds % 3600) // 60
        if days > 0:
            return f"⏳ باقیمانده: {days} روز و {hours} ساعت"
        if hours > 0:
            return f"⏳ باقیمانده: {hours} ساعت و {minutes} دقیقه"
        return f"⏳ باقیمانده: {minutes} دقیقه"
    except Exception:
        return "⏰ تاریخ انقضا نامعتبر"


def make_expiry_info_config(expires_at: str | None) -> str:
    """
    کانفیگ فیک برای نمایش مدت اعتبار در لیست کانفیگ‌های کلاینت.
    هر بار که مشتری ساب را آپدیت کند، با مقدار به‌روز ساخته می‌شود.
    """
    remark = remaining_time_text(expires_at)
    return (
        "vless://00000000-0000-0000-0000-000000000000@127.0.0.1:1"
        f"?encryption=none&security=none&type=tcp#{quote(remark)}"
    )


# ---------- متمایزسازی کانفیگ‌های هم‌فینگرپرینت (بدون حذف) ----------


def _split_query(raw: str) -> tuple[str, dict, str]:
    """base (قبل از ?) ، دیکشنری پارامترهای query ، fragment (بعد از #) رو برمی‌گردونه."""
    body, _, frag = raw.partition("#")
    base, sep, query = body.partition("?")
    params: dict[str, str] = {}
    if sep:
        for part in query.split("&"):
            if not part:
                continue
            k, _, v = part.partition("=")
            params[k] = v
    return base, params, frag


def _join_query(base: str, params: dict, frag: str) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    result = f"{base}?{query}" if query else base
    if frag:
        result += f"#{frag}"
    return result


def _disambiguate_one(raw: str, salt: int) -> str:
    """
    یه کپی «تکنیکی یکسان» رو با یه فیلد بی‌اثر (که رو مسیر واقعی اتصال تأثیر نداره)
    از بقیه متمایز می‌کنه، تا کلاینت اونا رو به‌جای یک نود، چند نود جدا ببینه.
    remark (اسم) دست‌نخورده می‌مونه.
    """
    proto = get_protocol(raw)

    if proto == "vmess":
        try:
            data = json.loads(_b64decode(raw[len("vmess://"):]))
            # فیلد ناشناخته — کلاینت‌های vmess فیلدهای ناآشنا رو نادیده می‌گیرن، رو مسیر تأثیر نداره
            data["_dd"] = salt
            new_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            new_b64 = base64.b64encode(new_json.encode("utf-8")).decode("utf-8")
            return f"vmess://{new_b64}"
        except Exception:
            return raw

    if "?" not in raw:
        return raw

    base, params, frag = _split_query(raw)
    if params.get("security") == "reality":
        # spx (spiderX) فقط رو ترافیک نمایشی reality اثر داره، نه رو تونل واقعی
        params["spx"] = quote(f"/{salt}")
    else:
        params["_dd"] = str(salt)
    return _join_query(base, params, frag)


def disambiguate_duplicates(configs: list[str]) -> list[str]:
    """
    اگه چند کانفیگ فینگرپرینت یکسان داشته باشن (یعنی از نظر اتصال واقعاً یکی‌ان،
    فقط اسمشون فرق داره)، نسخه‌های بعد از اولی رو با یه فیلد بی‌اثر متمایز می‌کنه
    تا کلاینت همه‌شون رو جدا نشون بده. هیچ کانفیگی حذف نمی‌شه، ترتیب حفظ می‌شه.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for raw in configs:
        fp = config_fingerprint(raw)
        count = seen.get(fp, 0)
        seen[fp] = count + 1
        if count == 0:
            result.append(raw)
        else:
            result.append(_disambiguate_one(raw, count))
    return result
