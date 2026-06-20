"""
Vietstock market report — chụp 6 tab + phân tích AI, gửi Telegram.
Chạy 15:30 ICT mỗi ngày giao dịch.

Usage:
  python -m scanner.foreign_flow [--force]
"""

from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta

from scanner.utils import logger

ICT           = timezone(timedelta(hours=7))
VIETSTOCK_URL = "https://finance.vietstock.vn/"
_NAV_HEIGHT   = 115   # px — cắt nav khi fallback viewport


@dataclass
class Section:
    tab:       str   # text tab để click
    icon:      str   # emoji cho header
    container: str   # CSS selector(s), phân cách bằng ","


SECTIONS = [
    Section("Bản đồ thị trường",   "🗺️",  "#visualization"),
    Section("Tổng hợp thị trường", "📊",  "#general-markets-container"),
    Section("Thanh khoản",          "💧",  "#liquidity-container"),
    Section("Ảnh hưởng Index",      "📈",  ".top-influence-box"),
    Section("Nước ngoài",           "🌍",  ".foreign-row"),
    Section("Tự doanh",             "🏦",  ".proprietary-row"),
]

_TIMEOUT_MS = 12_000


# ─── Entry point ─────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    from scanner.utils import is_trading_day
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua")
        return

    logger.info("=== Vietstock Market Report ===")
    try:
        results = _scrape_all()
    except Exception as e:
        logger.error(f"Scrape thất bại: {e}")
        _send_both(f"⚠️ <b>Market Report</b>: không scrape được\n<code>{e}</code>")
        return

    from scanner.telegram_bot import send_photo
    for section, img in results:
        caption = _gen_caption(section, img)
        send_photo(img, caption, style="long")
        send_photo(img, caption, style="short")
        logger.info(f"  Sent: {section.tab}")
        time.sleep(1)   # tránh flood Telegram

    logger.info(f"=== Xong {len(results)}/{len(SECTIONS)} sections ===")


# ─── Scraping ────────────────────────────────────────────────────────────────

def _scrape_all() -> list[tuple[Section, bytes]]:
    from playwright.sync_api import sync_playwright

    results: list[tuple[Section, bytes]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        page = browser.new_context(
            viewport={"width": 1440, "height": 860},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
        ).new_page()

        page.goto(VIETSTOCK_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_timeout(2_000)

        for section in SECTIONS:
            try:
                img = _scrape_section(page, section)
                results.append((section, img))
                logger.info(f"[OK] {section.tab}: {len(img)//1024} KB")
            except Exception as e:
                logger.warning(f"[SKIP] {section.tab}: {e}")

        browser.close()

    return results


def _scrape_section(page, section: Section) -> bytes:
    # Click tab
    for sel in [f"a:has-text('{section.tab}')", f"text={section.tab}"]:
        try:
            page.locator(sel).first.click(timeout=3_000)
            break
        except Exception:
            pass

    # Thử từng container selector
    for sel in (s.strip() for s in section.container.split(",")):
        try:
            page.wait_for_selector(sel, state="visible", timeout=_TIMEOUT_MS)
            page.wait_for_timeout(2_500)   # chờ chart/SVG render
            el = page.locator(sel).first
            el.scroll_into_view_if_needed()
            return el.screenshot()
        except Exception:
            pass

    # Fallback: cắt viewport bỏ nav (không cần Pillow)
    logger.debug(f"[{section.tab}] fallback viewport clip")
    page.wait_for_timeout(2_500)
    return page.screenshot(clip={
        "x": 0, "y": _NAV_HEIGHT, "width": 1440, "height": 860 - _NAV_HEIGHT
    })


# ─── Caption (vision AI) ─────────────────────────────────────────────────────

def _gen_caption(section: Section, img_bytes: bytes) -> str:
    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"{section.icon} <b>{section.tab} — {today}</b>"

    try:
        from groq import Groq
        from scanner.config import GROQ_API_KEY
        b64 = base64.b64encode(img_bytes).decode()
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            f"Đây là ảnh chụp màn hình phần '{section.tab}' "
                            f"từ trang Vietstock ngày {today}. "
                            "Viết 3-4 câu phân tích bằng tiếng Việt tự nhiên. "
                            "Không dùng gạch đầu dòng, không in đậm tên chỉ báo. "
                            "Nêu cụ thể số liệu, xu hướng và điểm đáng chú ý nhất."
                        ),
                    },
                ],
            }],
            max_tokens=300,
            temperature=0.4,
        )
        return f"{header}\n\n{resp.choices[0].message.content.strip()}"
    except Exception as e:
        logger.warning(f"Vision caption [{section.tab}]: {e}")
        return header


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _send_both(msg: str) -> None:
    try:
        from scanner.telegram_bot import send_message
        send_message(msg, style="long")
        send_message(msg, style="short")
    except Exception as e:
        logger.error(f"_send_both: {e}")


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    run(force="--force" in sys.argv)
