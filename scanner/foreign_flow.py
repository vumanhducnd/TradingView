"""
Foreign flow daily report — chụp ảnh Vietstock, viết caption AI, gửi Telegram.
Chạy sau 22:00 ICT mỗi ngày giao dịch.

Usage:
  python -m scanner.foreign_flow [--force]
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

from scanner.utils import logger

ICT          = timezone(timedelta(hours=7))
VIETSTOCK_URL = "https://finance.vietstock.vn/"
_CONTAINER   = ".foreign-row"          # class xác nhận bởi user
_TIMEOUT     = 15_000                  # ms chờ element


# ─── Entry point ─────────────────────────────────────────────────────────────

def run(force: bool = False) -> None:
    from scanner.utils import is_trading_day
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua foreign flow")
        return

    logger.info("=== Foreign Flow Report ===")
    try:
        screenshot, sell_rows, buy_rows = _scrape()
    except Exception as e:
        logger.error(f"Scrape thất bại: {e}")
        _send_both(f"⚠️ <b>Foreign flow</b>: không scrape được Vietstock\n<code>{e}</code>")
        return

    caption = _gen_caption(sell_rows, buy_rows)
    logger.info(f"Caption ({len(sell_rows)} bán / {len(buy_rows)} mua): {caption[:60]}...")

    from scanner.telegram_bot import send_photo
    send_photo(screenshot, caption, style="long")
    send_photo(screenshot, caption, style="short")
    logger.info("Đã gửi")


# ─── Scraping ────────────────────────────────────────────────────────────────

def _scrape() -> tuple[bytes, list[dict], list[dict]]:
    from playwright.sync_api import sync_playwright

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

        # Click tab "Nước ngoài"
        for sel in ["a:has-text('Nước ngoài')", "text=Nước ngoài", "[href*='nuoc-ngoai']"]:
            try:
                page.locator(sel).first.click(timeout=3_000)
                logger.info(f"Clicked: {sel}")
                break
            except Exception:
                pass

        # Chờ container visible, sau đó đợi thêm chart render
        page.wait_for_selector(_CONTAINER, state="visible", timeout=_TIMEOUT)
        page.wait_for_timeout(3_000)

        # Screenshot đúng element
        el = page.locator(_CONTAINER).first
        el.scroll_into_view_if_needed()
        screenshot = el.screenshot()
        logger.info(f"Screenshot {_CONTAINER}: {len(screenshot)//1024} KB")

        sell_rows, buy_rows = _extract_data(page)
        logger.info(f"Bán ròng: {len(sell_rows)} | Mua ròng: {len(buy_rows)}")

        browser.close()

    return screenshot, sell_rows, buy_rows


# ─── Data extraction ─────────────────────────────────────────────────────────

_JS_EXTRACT = """
() => {
    const root = document.querySelector('.foreign-row') || document;
    const result = { sell: [], buy: [] };

    // Ưu tiên Highcharts API (reliable nhất)
    if (window.Highcharts) {
        for (const chart of window.Highcharts.charts.filter(Boolean)) {
            if (!root.contains(chart.container)) continue;
            for (const series of chart.series) {
                if (!series.visible || !series.data.length) continue;
                series.data.forEach(pt => {
                    const cat = pt.category || (pt.name || '');
                    if (!/^[A-Z]{2,4}$/.test(cat)) return;
                    (pt.y < 0 ? result.sell : result.buy).push({
                        ticker: cat, value: Math.abs(pt.y)
                    });
                });
            }
        }
        if (result.sell.length || result.buy.length) return result;
    }

    // Fallback: SVG text pairs trong container
    const texts = Array.from(root.querySelectorAll('svg text'))
        .map(el => el.textContent.trim()).filter(Boolean);

    for (let i = 0; i < texts.length; i++) {
        if (!/^[A-Z]{2,4}$/.test(texts[i])) continue;
        // Tìm số trong vòng ±2 vị trí
        for (let d = 1; d <= 2; d++) {
            const v = parseFloat((texts[i - d] || '').replace(/,/g, ''));
            if (!isNaN(v) && v > 0) { result.sell.push({ ticker: texts[i], value: v }); break; }
            const v2 = parseFloat((texts[i + d] || '').replace(/,/g, ''));
            if (!isNaN(v2) && v2 > 0) { result.sell.push({ ticker: texts[i], value: v2 }); break; }
        }
    }

    // Tách đôi: bán (trái) / mua (phải) — Vietstock xếp theo thứ tự DOM trái→phải
    if (result.sell.length > 1 && !result.buy.length) {
        const mid = Math.ceil(result.sell.length / 2);
        result.buy  = result.sell.slice(mid);
        result.sell = result.sell.slice(0, mid);
    }

    return result;
}
"""


def _extract_data(page) -> tuple[list[dict], list[dict]]:
    try:
        data = page.evaluate(_JS_EXTRACT)
        return data.get("sell", []), data.get("buy", [])
    except Exception as e:
        logger.debug(f"extract_data: {e}")
        return [], []


# ─── Caption ─────────────────────────────────────────────────────────────────

def _gen_caption(sell_rows: list[dict], buy_rows: list[dict]) -> str:
    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"🌍 <b>Dòng vốn nước ngoài — {today}</b>"

    sell_text = ", ".join(f"{r['ticker']} (-{r['value']:.0f} tỷ)" for r in sell_rows[:6])
    buy_text  = ", ".join(f"{r['ticker']} (+{r['value']:.0f} tỷ)" for r in buy_rows[:6])

    if not sell_text and not buy_text:
        return header

    prompt = (
        f"Dữ liệu giao dịch ròng NĐTNN ngày {today}:\n"
        f"Bán ròng: {sell_text or '–'}\n"
        f"Mua ròng: {buy_text  or '–'}\n\n"
        "Viết 3-4 câu phân tích tiếng Việt tự nhiên (không bullet, không in đậm chỉ báo). "
        "Nêu cụ thể mã + số tỷ, ảnh hưởng VN-Index, nhận định dòng tiền nội."
    )

    try:
        from groq import Groq
        from scanner.config import GROQ_API_KEY
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=0.45,
        )
        return f"{header}\n\n{resp.choices[0].message.content.strip()}"
    except Exception as e:
        logger.warning(f"Groq failed: {e}")
        lines = [header]
        if sell_text: lines.append(f"📉 {sell_text}")
        if buy_text:  lines.append(f"📈 {buy_text}")
        return "\n".join(lines)


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
