"""
scanner/tv_screenshot.py

Chụp ảnh TradingView top 5 cổ phiếu sắp đến điểm mua hàng ngày.
2 trang mỗi mã: seasonals + forecast-price-target.
Gửi Telegram với caption AI (Groq vision).

Usage:
  python -m scanner.tv_screenshot [--force] [--debug] [--save]
  python -m scanner.tv_screenshot --tickers SSI,VIC,FPT  # override tickers
"""

from __future__ import annotations

import base64
import time
from datetime import date, datetime, timezone, timedelta

from scanner.utils import logger

ICT      = timezone(timedelta(hours=7))
_TV_BASE = "https://vn.tradingview.com/symbols"
_NAV_H   = 64   # pixel header TradingView cần cắt

TV_PAGES = [
    ("seasonals",             "📅 Mùa vụ"),
    ("forecast-price-target", "🎯 Dự báo giá"),
]

# Cổ phiếu VN100 trên HNX (số ít) — còn lại mặc định HOSE
_HNX_TICKERS = {
    "SHB", "NVB", "VIX", "CEO", "PVI", "PGS", "BVS",
    "HUT", "S99", "VCS", "SCI", "TNG", "MST",
}

_AI_STYLE = (
    "Chỉ viết 2-3 câu liên tiếp, không chia đoạn, văn phong bản tin chứng khoán. "
    "Không liệt kê máy móc, không gạch đầu dòng, không in đậm. "
    "Nêu nhận xét đầu tư cụ thể dựa trên dữ liệu trong ảnh."
)


# ─── Entry point ─────────────────────────────────────────────────────────────

def run(force: bool = False, top_n: int = 5, tickers: list[str] | None = None) -> None:
    from scanner.utils import is_trading_day
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua")
        return

    if tickers:
        # (ticker, exchange, score)
        stocks: list[tuple[str, str, float]] = [
            (t.upper(), _exchange(t.upper()), 0.0) for t in tickers
        ]
    else:
        stocks = _get_top_stocks(top_n)

    if not stocks:
        logger.warning("Không tìm thấy cổ phiếu thỏa tiêu chí — bỏ qua")
        return

    tickers_str = ", ".join(t for t, _, _ in stocks)
    logger.info(f"=== TV Screenshot: {tickers_str} ===")

    stock_pairs = [(t, e) for t, e, _ in stocks]
    results     = _scrape_all(stock_pairs)

    from scanner.telegram_bot import send_message, send_photo

    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    lines  = "\n".join(
        f"  #{i+1} <b>{t}</b>" + (f" (score: {s:.0f})" if s else "")
        for i, (t, _, s) in enumerate(stocks)
    )
    header = f"📸 <b>Top {len(stocks)} cổ phiếu sắp đến điểm mua — {today}</b>\n{lines}"

    send_message(header, style="long")
    send_message(header, style="short")
    time.sleep(1)

    for ticker, page_label, img in results:
        caption = _gen_caption(ticker, page_label, img)
        send_photo(img, caption, style="long")
        send_photo(img, caption, style="short")
        logger.info(f"  Sent: {ticker} / {page_label}")
        time.sleep(1)

    logger.info(f"=== Xong {len(results)} ảnh ===")


# ─── Lấy top cổ phiếu từ DB ──────────────────────────────────────────────────

def _get_top_stocks(top_n: int) -> list[tuple[str, str, float]]:
    """Trả về list (ticker, exchange, super_score) top N cổ phiếu sắp đến điểm mua."""
    import pandas as pd
    from scanner.database import db_cursor

    cols_needed = (
        "ticker, long_trend, bias_norm, b_score, close, "
        "long_buy_signal, short_buy_signal, long_sell_signal, short_sell_signal, "
        "short_trend, bull_vol, bull_macd, bull_rsi, bull_adx, avg_turnover_20d"
    )

    try:
        with db_cursor() as cur:
            cur.execute(f"SELECT {cols_needed} FROM scan_results WHERE long_trend IS NOT NULL")
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"DB query thất bại: {e}")
        return []

    if not rows:
        logger.warning("scan_results rỗng hoặc chưa có dữ liệu long_trend")
        return []

    df = pd.DataFrame([dict(r) for r in rows])

    from scanner.scanner import get_super_buy_stocks
    top = get_super_buy_stocks(df, top_n=top_n)

    if top.empty:
        logger.warning("get_super_buy_stocks trả về rỗng — không có mã thỏa tiêu chí")
        return []

    return [
        (row["ticker"], _exchange(row["ticker"]), float(row.get("super_score", 0.0)))
        for _, row in top.iterrows()
    ]


def _exchange(ticker: str) -> str:
    return "HNX" if ticker.upper() in _HNX_TICKERS else "HOSE"


def _tv_url(ticker: str, page_slug: str, exchange: str) -> str:
    return f"{_TV_BASE}/{exchange}-{ticker}/{page_slug}/"


# ─── Playwright scraping ──────────────────────────────────────────────────────

_TIMEOUT_MS = 15_000

def _crop_bytes(img_bytes: bytes, x1: int, y1: int, x2: int, y2: int) -> bytes:
    """Crop ảnh PNG bytes về vùng (x1,y1)-(x2,y2)."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(img_bytes))
        w, h = img.size
        box = (max(0, x1), max(0, y1), min(w, x2), min(h, y2))
        buf = io.BytesIO()
        img.crop(box).save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"_crop_bytes failed: {e}")
        return img_bytes


def _inject_ticker_label(page, ticker: str) -> None:
    """
    Ẩn logo TradingView (.js-copyright-label) bằng opacity:0 và chèn tên mã
    vào đúng parent container. Dùng MutationObserver để giữ hiệu lực qua
    mọi React re-render.
    """
    label_css = (
        "position:absolute;bottom:8px;left:8px;"
        "background:#7C3AED;color:#fff;"
        "font-size:16px;font-weight:700;"
        "padding:2px 10px;border-radius:3px;"
        "z-index:9999;pointer-events:none;"
        "font-family:Roboto,-apple-system,sans-serif;"
    )
    js = f"""() => {{
        const TICKER   = '{ticker}';
        const LBL_ID   = 'tv-ticker-label';
        const LBL_CSS  = '{label_css}';

        // Ẩn fixed/sticky navbar TradingView
        document.querySelectorAll('header, [data-name="header"]').forEach(el => {{
            el.style.setProperty('display', 'none', 'important');
        }});
        document.querySelectorAll('*').forEach(el => {{
            const s = window.getComputedStyle(el);
            if ((s.position === 'fixed' || s.position === 'sticky') &&
                    el.getBoundingClientRect().top < 80 &&
                    el.getBoundingClientRect().height < 80) {{
                el.style.setProperty('display', 'none', 'important');
            }}
        }});

        // Ẩn FAQ section (nằm sau financialsSection trong forecastPage)
        const financials = document.querySelector('[class*="financialsSection"]');
        if (financials) {{
            let sib = financials.nextElementSibling;
            while (sib) {{
                sib.style.setProperty('display', 'none', 'important');
                sib = sib.nextElementSibling;
            }}
        }}

        // Bỏ max-width để forecastPage mở rộng tự nhiên theo viewport
        document.querySelectorAll(
            '[class*="forecastPage"], [class*="pageWrap"], .forecast-root'
        ).forEach(el => {{
            el.style.setProperty('max-width', 'none',    'important');
            el.style.setProperty('overflow',  'visible', 'important');
        }});

        function patch() {{
            document.querySelectorAll('.js-copyright-label').forEach(logo => {{
                logo.style.setProperty('opacity',         '0',    'important');
                logo.style.setProperty('pointer-events', 'none', 'important');
                const p = logo.parentElement;
                if (p && !p.querySelector('#' + LBL_ID)) {{
                    const lbl = document.createElement('div');
                    lbl.id          = LBL_ID;
                    lbl.textContent = TICKER;
                    lbl.style.cssText = LBL_CSS;
                    p.appendChild(lbl);
                }}
            }});
        }}

        patch();
        new MutationObserver(patch).observe(
            document.body,
            {{childList: true, subtree: true}}
        );
    }}"""
    try:
        page.evaluate(js)
    except Exception as e:
        logger.debug(f"_inject_ticker_label failed: {e}")


# Selector element chính cần chụp (scroll vào + screenshot element, không clip pixel)
_CONTENT_SELECTOR: dict[str, list[str]] = {
    "seasonals": [
        "[data-qa-id='symbol-page-tab-seasonals-id-content']",  # stable data-qa-id
        "[class*='chartWrapper']",
    ],
    "forecast-price-target": [
        "[class*='forecastPage']",      # nội dung chính, bỏ company header + FAQ
        ".forecast-root",              # fallback
    ],
}

# Selector để biết trang đã render xong (có thể khác với _CONTENT_SELECTOR)
_READY_SELECTOR: dict[str, list[str]] = {
    "seasonals": [
        "[data-qa-id='symbol-page-tab-seasonals-id-content'] canvas",
        "[data-qa-id='symbol-page-tab-seasonals-id-content']",
        "canvas",
    ],
    "forecast-price-target": [
        "[class*='forecastPage'] canvas",
        "[class*='forecastPage']",
        "canvas",
    ],
}


def _scrape_all(
    stocks: list[tuple[str, str]],
    *,
    debug: bool = False,
) -> list[tuple[str, str, bytes]]:
    from playwright.sync_api import sync_playwright

    results: list[tuple[str, str, bytes]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not debug,
            slow_mo=600 if debug else 0,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="vi-VN",
        )
        page = ctx.new_page()

        _cookie_accepted = False

        for ticker, exchange in stocks:
            for page_slug, page_label in TV_PAGES:
                url = _tv_url(ticker, page_slug, exchange)
                try:
                    img, _cookie_accepted = _scrape_page(
                        page, url, page_slug=page_slug, ticker=ticker,
                        cookie_accepted=_cookie_accepted, debug=debug
                    )
                    results.append((ticker, page_label, img))
                    logger.info(f"[OK] {ticker}/{page_slug}: {len(img)//1024} KB")
                except Exception as e:
                    logger.warning(f"[SKIP] {ticker}/{page_slug}: {e}")

        if debug:
            page.wait_for_timeout(5_000)
        browser.close()

    return results


_COOKIE_BTNS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
    "button:has-text('OK')",
    "button:has-text('Chấp nhận tất cả')",
    "button:has-text('Chấp nhận')",
    "[id*='accept']:visible",
]


def _dismiss_cookie(page, *, debug: bool = False) -> bool:
    for sel in _COOKIE_BTNS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=800):
                btn.click()
                page.wait_for_timeout(600)
                if debug:
                    logger.info(f"[debug] Cookie dismissed via {sel!r}")
                return True
        except Exception:
            pass
    return False




def _scrape_page(
    page,
    url: str,
    *,
    page_slug: str = "",
    ticker: str = "",
    cookie_accepted: bool = False,
    debug: bool = False,
) -> tuple[bytes, bool]:
    """
    Navigate to url, wait for chart content, screenshot.
    Returns (image_bytes, cookie_accepted).
    """
    page.goto(url, wait_until="domcontentloaded", timeout=60_000)
    page.wait_for_timeout(2_500)

    if not cookie_accepted:
        accepted = _dismiss_cookie(page, debug=debug)
        cookie_accepted = cookie_accepted or accepted
        if accepted:
            page.wait_for_timeout(1_000)

    # 1. Đợi trang render xong
    for sel in _READY_SELECTOR.get(page_slug, ["canvas"]):
        try:
            page.wait_for_selector(sel, state="visible", timeout=_TIMEOUT_MS)
            if debug:
                logger.info(f"[debug] Ready selector found: {sel!r}")
            break
        except Exception:
            pass

    # 2. Thêm thời gian để canvas vẽ xong
    page.wait_for_timeout(3_000)

    # 3. Ẩn logo TV, chèn tên mã — MutationObserver giữ hiệu lực qua re-render
    if ticker:
        _inject_ticker_label(page, ticker)
        page.wait_for_timeout(1_500)   # đủ để Observer xử lý các re-render

    # 4. Tìm element chính, scroll vào và chụp
    for sel in _CONTENT_SELECTOR.get(page_slug, []):
        try:
            el = page.locator(sel).first
            if not el.is_visible(timeout=2_000):
                continue
            el.scroll_into_view_if_needed()
            page.wait_for_timeout(800)

            if page_slug == "forecast-price-target":
                # Resize viewport height để chứa đủ nội dung, chụp full-width từ x=0
                bb  = el.bounding_box()
                vp  = page.viewport_size or {"width": 1920, "height": 1000}
                new_h = int(bb["y"] + bb["height"]) + 60
                page.set_viewport_size({"width": vp["width"], "height": new_h})
                page.wait_for_timeout(400)
                bb2 = el.bounding_box()
                img = page.screenshot(clip={
                    "x":      0,
                    "y":      int(max(0, bb2["y"])),
                    "width":  vp["width"],
                    "height": int(bb2["height"]),
                })
            else:
                img = el.screenshot()

            if debug:
                bb = el.bounding_box()
                logger.info(f"[debug] Screenshot {sel!r}: {bb}, {len(img)//1024} KB")
            return img, cookie_accepted
        except Exception as exc:
            if debug:
                logger.info(f"[debug] Selector {sel!r} failed: {exc}")

    # Fallback: clip bỏ header TradingView
    if debug:
        logger.warning("[debug] Fallback clip — khong tim duoc content element")
    img = page.screenshot(
        clip={"x": 0, "y": _NAV_H, "width": 1440, "height": 1000 - _NAV_H}
    )
    if debug:
        logger.info(f"[debug] Fallback screenshot: {len(img)//1024} KB — {url}")
    return img, cookie_accepted


# ─── AI Caption ──────────────────────────────────────────────────────────────

_FOCUS: dict[str, str] = {
    "seasonals": (
        "Ảnh là biểu đồ mùa vụ (seasonal) của cổ phiếu: mỗi đường là một năm riêng biệt "
        "(màu sắc khác nhau, có nhãn năm + % tổng năm ở bên phải), trục X là 12 tháng, "
        "trục Y là % thay đổi lũy kế từ đầu năm. Đường năm hiện tại (2026) chạy đến giữa "
        "chừng vì năm chưa kết thúc. "
        "Hãy viết 3-4 câu phân tích theo 3 góc: "
        "(1) So sánh xu hướng các năm trước trong giai đoạn tháng hiện tại — các năm có hiệu suất tốt (đường cao) "
        "thường làm gì sau tháng này? "
        "(2) Đường năm 2026 hiện đang ở mức nào so với cùng kỳ các năm và có dấu hiệu sắp bứt phá lên không? "
        "(3) Kết luận: tháng tới theo lịch sử mùa vụ thường là giai đoạn tăng hay giảm, "
        "và xác suất cổ phiếu sắp đến điểm tăng là cao hay thấp. "
        "Nêu cụ thể tên năm, tháng và con số %."
    ),
    "forecast-price-target": (
        "Ảnh là trang dự báo giá mục tiêu (price target) của analyst, "
        "gồm biểu đồ giá hiện tại so với giá mục tiêu đồng thuận và phân bổ khuyến nghị "
        "(mua/giữ/bán). "
        "Viết 2-3 câu nhận xét: giá hiện tại đang thấp hơn hay cao hơn giá mục tiêu bao nhiêu %, "
        "tỷ lệ analyst khuyến nghị mua là bao nhiêu, và đánh giá chung tiềm năng tăng giá."
    ),
}


def _gen_caption(ticker: str, page_label: str, img_bytes: bytes) -> str:
    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"{page_label} <b>{ticker} — {today}</b>"

    # Xác định page_slug từ page_label
    slug = "seasonals" if "Mùa vụ" in page_label else "forecast-price-target"
    focus = _FOCUS.get(slug, "Mô tả ngắn nội dung chính của ảnh.")

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
                        "text": f"{focus} {_AI_STYLE}",
                    },
                ],
            }],
            max_tokens=200,
            temperature=0.4,
        )
        body    = resp.choices[0].message.content.strip()
        caption = f"{header}\n\n{body}"
        if len(caption) > 1024:
            caption = caption[:1021] + "..."
        return caption
    except Exception as e:
        logger.warning(f"Vision caption [{ticker}/{slug}]: {e}")
        return header


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    import pathlib

    args   = sys.argv[1:]
    force  = "--force" in args
    debug  = "--debug" in args
    save   = "--save"  in args

    # --tickers SSI,VIC,FPT
    tickers: list[str] | None = None
    for i, a in enumerate(args):
        if a == "--tickers" and i + 1 < len(args):
            tickers = [t.strip() for t in args[i + 1].split(",")]

    # --top N
    top_n = 5
    for i, a in enumerate(args):
        if a == "--top" and i + 1 < len(args):
            try:
                top_n = int(args[i + 1])
            except ValueError:
                pass

    from scanner.utils import is_trading_day

    if save or debug:
        out = pathlib.Path("screenshots")
        out.mkdir(exist_ok=True)

        if not force and not is_trading_day(date.today()):
            logger.info("Không phải ngày giao dịch — bỏ qua (dùng --force để ép)")
            sys.exit(0)

        if tickers:
            stocks = [(t.upper(), _exchange(t.upper()), 0.0) for t in tickers]
        else:
            stocks = _get_top_stocks(top_n)

        if not stocks:
            logger.warning("Không có cổ phiếu thỏa tiêu chí")
            sys.exit(0)

        logger.info(f"=== TV Screenshot [{', '.join(t for t,_,_ in stocks)}] ===")
        stock_pairs = [(t, e) for t, e, _ in stocks]
        results = _scrape_all(stock_pairs, debug=debug)

        for ticker, page_label, img in results:
            slug  = "seasonals" if "Mùa vụ" in page_label else "forecast"
            fname = out / f"{ticker}_{slug}.png"
            fname.write_bytes(img)
            logger.info(f"  Saved: {fname} ({len(img)//1024} KB)")

        logger.info(f"=== Xong {len(results)} anh — xem trong screenshots/ ===")
    else:
        run(force=force, top_n=top_n, tickers=tickers)
