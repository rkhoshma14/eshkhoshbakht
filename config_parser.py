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
