"""
تست پینگ (TCP connect latency) برای کانفیگ‌های یک اشتراک، به صورت موازی.
"""
import asyncio
import time

from config_parser import get_host_port

CONCURRENCY = 15
TIMEOUT = 5


async def _tcp_ping(host: str, port: int, timeout: float = TIMEOUT) -> float | None:
    start = time.monotonic()
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except Exception:
        return None

    elapsed_ms = (time.monotonic() - start) * 1000
    writer.close()
    try:
        await writer.wait_closed()
    except Exception:
        pass
    return elapsed_ms


async def ping_configs(configs: list[str]) -> dict[int, float | None]:
    """برای هر کانفیگ (با ایندکسش) لتنسی TCP رو به میلی‌ثانیه برمی‌گردونه، یا None اگه ناموفق بود."""
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(i: int, raw: str) -> tuple[int, float | None]:
        async with sem:
            hp = get_host_port(raw)
            if not hp:
                return i, None
            host, port = hp
            ms = await _tcp_ping(host, port)
            return i, ms

    results = await asyncio.gather(*(one(i, raw) for i, raw in enumerate(configs)))
    return dict(results)
