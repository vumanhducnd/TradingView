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
from typing import Callable
from datetime import date, datetime, timezone, timedelta

from scanner.utils import logger

ICT      = timezone(timedelta(hours=7))
_TV_BASE = "https://vn.tradingview.com/symbols"
_NAV_H   = 64   # pixel header TradingView cần cắt

TV_PAGES = [
    ("seasonals",             "📅 Mùa vụ"),
    ("forecast-price-target", "🎯 Dự báo giá"),
]

_exchange_cache: dict[str, str] = {}  # cache trong session

# ─── Daily image/caption cache (PostgreSQL) ───────────────────────────────────

_CACHE_TABLE = "tv_screenshot_cache"


def _ensure_cache_table() -> None:
    from scanner.database import db_cursor
    try:
        with db_cursor(commit=True) as cur:
            cur.execute(f"""
                CREATE TABLE IF NOT EXISTS {_CACHE_TABLE} (
                    ticker     VARCHAR(20) NOT NULL,
                    cache_date DATE        NOT NULL,
                    slug       VARCHAR(50) NOT NULL,
                    img_bytes  BYTEA,
                    caption    TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (ticker, cache_date, slug)
                )
            """)
            cur.execute(
                f"DELETE FROM {_CACHE_TABLE} WHERE created_at < NOW() - INTERVAL '1 day'"
            )
    except Exception as e:
        logger.warning(f"Cache table init failed: {e}")


def _cache_get(ticker: str, cache_date: date) -> "tuple[bytes, bytes, str] | None":
    """Return (img_seasonal, img_forecast, caption) nếu cache đầy đủ, else None."""
    from scanner.database import db_cursor
    try:
        with db_cursor(commit=False) as cur:
            cur.execute(
                f"SELECT slug, img_bytes, caption FROM {_CACHE_TABLE} "
                "WHERE ticker=%s AND cache_date=%s",
                (ticker, cache_date),
            )
            rows = {r["slug"]: r for r in cur.fetchall()}
        img_s = bytes(rows["seasonals"]["img_bytes"]) if rows.get("seasonals") and rows["seasonals"]["img_bytes"] else None
        img_f = bytes(rows["forecast-price-target"]["img_bytes"]) if rows.get("forecast-price-target") and rows["forecast-price-target"]["img_bytes"] else None
        caption = (rows.get("_caption") or {}).get("caption")
        if img_s and img_f and caption:
            return img_s, img_f, caption
        return None
    except Exception:
        return None


def _cache_put(ticker: str, cache_date: date, img_s: bytes, img_f: bytes, caption: str) -> None:
    from scanner.database import db_cursor
    try:
        with db_cursor(commit=True) as cur:
            for slug, img in [("seasonals", img_s), ("forecast-price-target", img_f)]:
                cur.execute(
                    f"""INSERT INTO {_CACHE_TABLE} (ticker, cache_date, slug, img_bytes)
                        VALUES (%s,%s,%s,%s)
                        ON CONFLICT (ticker, cache_date, slug)
                        DO UPDATE SET img_bytes=EXCLUDED.img_bytes, created_at=NOW()""",
                    (ticker, cache_date, slug, img),
                )
            cur.execute(
                f"""INSERT INTO {_CACHE_TABLE} (ticker, cache_date, slug, caption)
                    VALUES (%s,%s,'_caption',%s)
                    ON CONFLICT (ticker, cache_date, slug)
                    DO UPDATE SET caption=EXCLUDED.caption, created_at=NOW()""",
                (ticker, cache_date, caption),
            )
        logger.debug(f"Cache saved: {ticker} {cache_date}")
    except Exception as e:
        logger.warning(f"Cache put failed [{ticker}]: {e}")

NEAR_BUY_THRESHOLD_PCT = 3.0  # % cách ST tối đa để coi là "gần điểm mua"
_TK_MIN_TY             = 10.0  # thanh khoản tối thiểu (tỷ VND)

_AI_STYLE = (
    "Chỉ viết 2-3 câu liên tiếp, không chia đoạn, văn phong bản tin chứng khoán. "
    "Không liệt kê máy móc, không gạch đầu dòng, không in đậm. "
    "Nêu nhận xét đầu tư cụ thể dựa trên dữ liệu trong ảnh."
)


# ─── Entry point ─────────────────────────────────────────────────────────────

_STYLE_LABEL = {"long": "Dài hạn", "short": "Ngắn hạn"}


def run(
    force: bool = False,
    top_n: int = 5,
    tickers: list[str] | None = None,
    mode: str = "top",
    stocks: list[tuple[str, str, float]] | None = None,
    send_style: str | None = None,   # "long" | "short" | None = gửi cả 2
) -> None:
    from scanner.utils import is_trading_day
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua")
        return

    if stocks is not None:
        pass  # dùng luôn list truyền vào
    elif tickers:
        stocks = [
            (t.upper(), _exchange(t.upper()), 0.0) for t in tickers
        ]
    elif mode == "near_buy":
        stocks = _get_near_buy_stocks(top_n)
    else:
        stocks = _get_top_stocks(top_n)

    if not stocks:
        logger.warning("Không tìm thấy cổ phiếu thỏa tiêu chí — bỏ qua")
        return

    send_groups = [send_style] if send_style else ["long", "short"]
    tickers_str = ", ".join(t for t, _, _ in stocks)
    logger.info(f"=== TV Screenshot [{mode}][nhóm {'/'.join(send_groups)}]: {tickers_str} ===")

    # Check daily cache
    today_ict = datetime.now(ICT).date()
    _ensure_cache_table()
    hit: list[tuple[str, bytes, bytes, str]] = []   # (ticker, img_s, img_f, caption)
    uncached: list[tuple[str, str, float]] = []
    for stock in stocks:
        t = stock[0]
        cached = _cache_get(t, today_ict)
        if cached:
            logger.info(f"[CACHE HIT] {t}")
            hit.append((t, *cached))
        else:
            uncached.append(stock)

    from scanner.telegram_bot import send_message, send_photo, send_photo_group

    today = datetime.now(ICT).strftime("%d/%m/%Y")

    if mode == "near_buy":
        style_suffix = f" — {_STYLE_LABEL[send_style]}" if send_style else ""
        lines = "\n".join(
            f"  #{i+1} <b>{t}</b>" + (f" (cách ST: {s:.1f}%)" if s > 0 else "")
            for i, (t, _, s) in enumerate(stocks)
        )
        header = f"🔍 <b>Top {len(stocks)} cổ phiếu gần điểm MUA{style_suffix} — {today}</b>\n{lines}"
    else:
        lines = "\n".join(
            f"  #{i+1} <b>{t}</b>" + (f" (score: {s:.0f})" if s else "")
            for i, (t, _, s) in enumerate(stocks)
        )
        header = f"📸 <b>Top {len(stocks)} cổ phiếu sắp đến điểm mua — {today}</b>\n{lines}"

    for g in send_groups:
        send_message(header, style=g)
    time.sleep(1)

    # Gửi từ cache
    for t, img_s, img_f, caption in hit:
        for g in send_groups:
            send_photo_group([img_s, img_f], caption, style=g)
        logger.info(f"  [CACHE] Sent: {t} → nhóm {'+'.join(send_groups)}")
        time.sleep(1)

    # Scrape + gửi mã chưa có cache
    if uncached:
        stock_pairs = [(t, e) for t, e, _ in uncached]
        results     = _scrape_all(stock_pairs)
        _send_scraped(results, groups_for=lambda _: send_groups, cache_date=today_ict)

    logger.info(f"=== Xong (cache={len(hit)}, mới={len(uncached)}) ===")


def _send_scraped(
    scraped: list[tuple[str, str, bytes]],
    groups_for: "Callable[[str], list[str]]",
    *,
    cache_date: "date | None" = None,
) -> None:
    """Group scraped results theo ticker → gửi album 2 ảnh + 1 caption tổng hợp."""
    from collections import defaultdict
    from scanner.telegram_bot import send_photo, send_photo_group

    # Gom ảnh theo ticker, giữ thứ tự xuất hiện
    by_ticker: dict[str, dict[str, bytes]] = defaultdict(dict)
    order: list[str] = []
    for ticker, page_label, img in scraped:
        if ticker not in by_ticker:
            order.append(ticker)
        slug = "seasonals" if "Mùa vụ" in page_label else "forecast-price-target"
        by_ticker[ticker][slug] = img

    for ticker in order:
        groups = groups_for(ticker)
        if not groups:
            continue
        imgs  = by_ticker[ticker]
        img_s = imgs.get("seasonals")
        img_f = imgs.get("forecast-price-target")

        if img_s and img_f:
            caption = _gen_caption_pair(ticker, img_s, img_f)
            if cache_date:
                _cache_put(ticker, cache_date, img_s, img_f, caption)
            for g in groups:
                send_photo_group([img_s, img_f], caption, style=g)
        else:
            # Fallback: còn thiếu 1 ảnh → gửi từng cái riêng
            for slug, img in imgs.items():
                label   = "📅 Mùa vụ" if slug == "seasonals" else "🎯 Dự báo giá"
                caption = _gen_caption(ticker, label, img)
                for g in groups:
                    send_photo(img, caption, style=g)

        logger.info(f"  Sent: {ticker} (album) → nhóm {'+'.join(groups)}")
        time.sleep(1)


def run_near_buy_dual(
    stocks_long: list[tuple[str, str, float]],
    stocks_short: list[tuple[str, str, float]],
    force: bool = False,
) -> None:
    """
    Chụp TradingView cho near_buy long + short.
    Ticker chung cả 2 style → chụp 1 lần, gửi cả 2 nhóm.
    Ticker riêng → chụp 1 lần, gửi đúng nhóm.
    """
    from scanner.utils import is_trading_day
    if not force and not is_trading_day(date.today()):
        logger.info("Hôm nay không phải ngày giao dịch — bỏ qua")
        return

    from scanner.telegram_bot import send_message

    long_map  = {t: (e, s) for t, e, s in stocks_long}
    short_map = {t: (e, s) for t, e, s in stocks_short}

    common     = sorted(set(long_map) & set(short_map))
    only_long  = [t for t, _, _ in stocks_long  if t not in short_map]
    only_short = [t for t, _, _ in stocks_short if t not in long_map]

    all_pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group, src_map in [(common, long_map), (only_long, long_map), (only_short, short_map)]:
        for t in group:
            if t not in seen:
                all_pairs.append((t, src_map[t][0]))
                seen.add(t)

    if not all_pairs:
        logger.info("TV Screenshot dual: không có mã nào — bỏ qua")
        return

    # Check daily cache
    today_ict = datetime.now(ICT).date()
    _ensure_cache_table()
    hit: list[tuple[str, bytes, bytes, str]] = []
    uncached_pairs: list[tuple[str, str]] = []
    for t, e in all_pairs:
        cached = _cache_get(t, today_ict)
        if cached:
            logger.info(f"[CACHE HIT] {t}")
            hit.append((t, *cached))
        else:
            uncached_pairs.append((t, e))

    logger.info(
        f"=== TV Screenshot dual: {len(all_pairs)} mã "
        f"(chung={len(common)}, chỉ_long={len(only_long)}, chỉ_short={len(only_short)}) "
        f"[cache={len(hit)}, mới={len(uncached_pairs)}] ==="
    )

    today = datetime.now(ICT).strftime("%d/%m/%Y")

    def _header(stocks: list[tuple[str, str, float]], style_key: str) -> str:
        label = _STYLE_LABEL[style_key]
        lines = "\n".join(
            f"  #{i+1} <b>{t}</b>" + (f" (cách ST: {s:.1f}%)" if s > 0 else "")
            for i, (t, _, s) in enumerate(stocks)
        )
        return f"🔍 <b>Top {len(stocks)} cổ phiếu gần điểm MUA — {label} — {today}</b>\n{lines}"

    if stocks_long:
        send_message(_header(stocks_long, "long"), style="long")
    if stocks_short:
        send_message(_header(stocks_short, "short"), style="short")
    time.sleep(1)

    def _groups_for(ticker: str) -> list[str]:
        gs = []
        if ticker in long_map:  gs.append("long")
        if ticker in short_map: gs.append("short")
        return gs

    # Gửi từ cache
    from scanner.telegram_bot import send_photo_group
    for t, img_s, img_f, caption in hit:
        groups = _groups_for(t)
        for g in groups:
            send_photo_group([img_s, img_f], caption, style=g)
        logger.info(f"  [CACHE] Sent: {t} → nhóm {'+'.join(groups)}")
        time.sleep(1)

    # Scrape + gửi mã chưa có cache
    if uncached_pairs:
        scraped = _scrape_all(uncached_pairs)
        _send_scraped(scraped, groups_for=_groups_for, cache_date=today_ict)

    logger.info(f"=== Xong {len(all_pairs)} mã (cache={len(hit)}, Playwright={len(uncached_pairs)}) ===")


# ─── Lấy top cổ phiếu từ DB ──────────────────────────────────────────────────

def _get_top_stocks(top_n: int) -> list[tuple[str, str, float]]:
    """Top N mã long_trend=1, sort theo bias_norm — giống logic /top trong bot."""
    from scanner.database import db_cursor

    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT ticker, bias_norm
                FROM scan_results
                WHERE long_trend = 1
                ORDER BY bias_norm DESC
                LIMIT %s
            """, (top_n,))
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"DB query thất bại: {e}")
        return []

    if not rows:
        logger.warning("Không có mã nào long_trend=1 trong scan_results")
        return []

    return [
        (r["ticker"], _exchange(r["ticker"]), float(r.get("bias_norm", 0.0)))
        for r in rows
    ]


def _get_near_buy_stocks(top_n: int) -> list[tuple[str, str, float]]:
    """Top N mã long_trend=-1 gần SuperTrend nhất (chờ lật lên MUA).
    Score trả về = dist_pct (% cách ST từ dưới lên), thấp hơn = gần hơn.
    """
    from scanner.database import db_cursor

    try:
        with db_cursor() as cur:
            cur.execute("""
                SELECT ticker,
                       ROUND(((long_supertrend - close) / long_supertrend * 100)::numeric, 2)
                           AS dist_pct
                FROM scan_results
                WHERE long_trend = -1
                  AND long_supertrend > 0
                  AND close > 0
                  AND (long_supertrend - close) / long_supertrend * 100
                      BETWEEN 0 AND %s
                  AND turnover / 1e9 >= %s
                ORDER BY dist_pct ASC
                LIMIT %s
            """, (NEAR_BUY_THRESHOLD_PCT, _TK_MIN_TY, top_n))
            rows = cur.fetchall()
    except Exception as e:
        logger.error(f"DB query near_buy thất bại: {e}")
        return []

    if not rows:
        logger.warning(f"Không có mã nào near_buy (dist ≤ {NEAR_BUY_THRESHOLD_PCT}%)")
        return []

    return [
        (r["ticker"], _exchange(r["ticker"]), float(r["dist_pct"]))
        for r in rows
    ]


def _exchange(ticker: str) -> str:
    """Tra sàn từ watchlist DB, cache trong session, fallback HOSE."""
    t = ticker.upper()
    if t not in _exchange_cache:
        try:
            from scanner.database import load_exchange_map
            _exchange_cache.update(load_exchange_map([t]))
        except Exception:
            pass
    return _exchange_cache.get(t, "HOSE") or "HOSE"


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
        "Ảnh là biểu đồ mùa vụ (seasonal) của cổ phiếu. "
        "QUAN TRỌNG — cấu trúc ảnh: "
        "Phía trên cùng có một THANH KÉO THỜI GIAN (range slider) hiển thị các năm từ 2015 đến nay — "
        "TUYỆT ĐỐI BỎ QUA thanh này, nó không phải nhãn của các đường. "
        "Phần chính là biểu đồ gồm nhiều đường màu sắc khác nhau: mỗi đường là một năm, "
        "trục X là 12 tháng (Tháng 1 → Tháng 12), trục Y là % thay đổi lũy kế từ đầu năm. "
        "Nhãn năm và % THỰC SỰ chỉ nằm ở CỘT BÊN PHẢI sát mép ảnh (ví dụ: 2025 105%, 2026 58%, ...). "
        "Đường ngắn nhất/chưa đến cuối năm là năm hiện tại (2026). "
        "Chỉ phân tích CÁC ĐƯỜNG và nhãn bên phải, không đọc thanh kéo trên đầu. "
        "Hãy viết 3-4 câu phân tích tập trung vào 1-2 THÁNG TIẾP THEO từ điểm cuối đường 2026: "
        "(1) Nhìn vào đoạn 1-2 tháng ngay sau vị trí hiện tại trên trục X: "
        "với 5 năm gần nhất (đọc tên từ nhãn bên phải), liệt kê RÕ RÀNG từng năm — "
        "năm nào có đường đi LÊN và năm nào có đường đi XUỐNG trong đoạn 1-2 tháng tiếp theo đó. "
        "Ví dụ: '2021, 2023 đi lên; 2022, 2024, 2025 đi xuống — tỉ lệ 2/5 năm tăng'. "
        "Nêu năm tăng mạnh nhất và tăng bao nhiêu %, năm giảm mạnh nhất và giảm bao nhiêu %. "
        "(2) Đường 2026 đang ở mức % nào (nhãn bên phải), xu hướng gần đây đang lên hay xuống? "
        "(3) Kết luận: trong 1-2 tháng tới lịch sử nghiêng về tăng hay giảm, "
        "mức tăng/giảm trung bình kỳ vọng khoảng bao nhiêu %, và năm nào là kịch bản tốt nhất để tham chiếu. "
        "Nêu rõ tên năm và con số %, không dùng từ mơ hồ."
    ),
    "forecast-price-target": (
        "Ảnh gồm: (1) biểu đồ giá lịch sử 2 năm + dự báo 1 năm tới với vùng hình nón "
        "(min/trung bình/max), (2) đồng hồ khuyến nghị analyst (Mua mạnh/Mua/Giữ/Bán), "
        "(3) biểu đồ EPS và Doanh thu thực tế vs ước tính theo năm. "
        "Hãy viết 3-4 câu phân tích dựa trực tiếp vào các chỉ số và dự báo trong ảnh: "
        "(1) Dựa vào biểu đồ EPS trong ảnh: EPS thực tế gần nhất so với ước tính đang vượt hay dưới, "
        "xu hướng EPS đang tăng hay giảm qua các quý/năm? "
        "(2) Dựa vào biểu đồ Doanh thu trong ảnh: doanh thu thực tế gần nhất có vượt ước tính không, "
        "và dự báo doanh thu các năm tới đang tăng hay giảm? "
        "(3) Vùng dự báo giá 1 năm tới (hình nón min/trung bình/max) đang hướng lên hay đi ngang, "
        "và đồng hồ khuyến nghị analyst đang ở mức nào (Mua mạnh/Mua/Giữ/Bán)? "
        "Không đề cập đến con số giá tuyệt đối. "
        "Cuối cùng thêm câu: 'Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.'"
    ),
}


def _gen_caption(ticker: str, page_label: str, img_bytes: bytes) -> str:
    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"{page_label} <b>{ticker} — {today}</b>"

    slug  = "seasonals" if "Mùa vụ" in page_label else "forecast-price-target"
    focus = _FOCUS.get(slug, "Mô tả ngắn nội dung chính của ảnh.")
    prompt = f"{focus} {_AI_STYLE}"

    b64  = base64.b64encode(img_bytes).decode()
    body = _vision_groq(b64, prompt) or _vision_openai(b64, prompt)

    if not body:
        logger.warning(f"Vision caption [{ticker}/{slug}]: cả Groq lẫn OpenAI đều thất bại")
        return header

    caption = f"{header}\n\n{body}"
    if len(caption) > 1024:
        disclaimer = "Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."
        caption    = caption[:980] + "...\n" + disclaimer
    return caption


_PROMPT_PAIR = (
    "Có 2 ảnh phân tích cùng 1 cổ phiếu: "
    "Ảnh 1 là biểu đồ mùa vụ (seasonal): phía trên có thanh kéo thời gian — BỎ QUA thanh đó. "
    "Phần chính gồm các đường màu, mỗi đường là 1 năm, trục X là 12 tháng, trục Y là % lũy kế. "
    "Nhãn năm và % CHỈ nằm ở cột bên phải sát mép ảnh — chỉ đọc nhãn từ đó. "
    "Đường ngắn nhất (chưa đến tháng 12) là năm hiện tại 2026. "
    "Ảnh 2 là dự báo giá 1 năm tới (hình nón min/trung bình/max) + đồng hồ khuyến nghị analyst + biểu đồ EPS/Doanh thu. "
    "Hãy viết 3-4 câu tổng hợp tập trung vào 1-2 THÁNG TIẾP THEO: "
    "(1) Nhìn ảnh mùa vụ tại điểm cuối đường 2026: với 5 năm gần nhất (đọc tên từ nhãn bên phải), "
    "liệt kê RÕ RÀNG năm nào đi LÊN và năm nào đi XUỐNG trong đoạn 1-2 tháng tiếp theo từ vị trí đó. "
    "Ví dụ: '2021, 2023 đi lên; 2022, 2024, 2025 đi xuống — tỉ lệ 2/5 năm tăng'. "
    "Nêu năm tăng mạnh nhất trong giai đoạn này là năm nào và tăng bao nhiêu %. "
    "(2) Dựa vào biểu đồ EPS và Doanh thu trong ảnh 2: EPS thực tế gần nhất đang vượt hay dưới ước tính, "
    "xu hướng EPS tăng hay giảm; doanh thu thực tế có vượt kỳ vọng không; "
    "vùng dự báo giá 1 năm tới hướng lên hay đi ngang và đồng hồ analyst đang ở mức nào? "
    "(3) Kết luận: lịch sử mùa vụ từ tháng này ủng hộ tăng hay giảm, "
    "kết hợp với chỉ số tài chính từ ảnh 2, đây là thời điểm tốt để mua hay cần chờ? "
    "Cuối cùng thêm câu: 'Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư.' "
    "Chỉ viết liên tục không gạch đầu dòng, không in đậm, văn phong bản tin chứng khoán."
)


def _gen_caption_pair(ticker: str, img_seasonal: bytes, img_forecast: bytes) -> str:
    """Gửi cả 2 ảnh cho vision model, trả về 1 caption tổng hợp."""
    today  = datetime.now(ICT).strftime("%d/%m/%Y")
    header = f"<b>{ticker} — {today}</b>"

    b64_s = base64.b64encode(img_seasonal).decode()
    b64_f = base64.b64encode(img_forecast).decode()

    body = _vision_groq_pair(b64_s, b64_f, _PROMPT_PAIR) or _vision_openai_pair(b64_s, b64_f, _PROMPT_PAIR)

    if not body:
        logger.warning(f"Vision pair [{ticker}]: cả Groq lẫn OpenAI đều thất bại")
        return header

    caption = f"{header}\n\n{body}"
    if len(caption) > 1024:
        disclaimer = "Thông tin chỉ mang tính tham khảo, không phải khuyến nghị đầu tư."
        caption    = caption[:980] + "...\n" + disclaimer
    return caption


def _vision_groq_pair(b64_s: str, b64_f: str, prompt: str) -> str | None:
    try:
        from groq import Groq
        from scanner.config import GROQ_API_KEY
        if not GROQ_API_KEY:
            return None
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_s}"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_f}"}},
                {"type": "text",      "text": prompt},
            ]}],
            max_tokens=400,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq vision pair failed: {e} — thử OpenAI")
        return None


def _vision_openai_pair(b64_s: str, b64_f: str, prompt: str) -> str | None:
    try:
        from openai import OpenAI
        from scanner.config import OPENAI_API_KEY
        if not OPENAI_API_KEY:
            return None
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_s}", "detail": "low"}},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_f}", "detail": "low"}},
                {"type": "text",      "text": prompt},
            ]}],
            max_tokens=400,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"OpenAI vision pair failed: {e}")
        return None


def _vision_groq(b64: str, prompt: str) -> str | None:
    try:
        from groq import Groq
        from scanner.config import GROQ_API_KEY
        if not GROQ_API_KEY:
            return None
        resp = Groq(api_key=GROQ_API_KEY).chat.completions.create(
            model="meta-llama/llama-4-scout-17b-16e-instruct",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text",      "text": prompt},
            ]}],
            max_tokens=300,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"Groq vision failed: {e} — thử OpenAI")
        return None


def _vision_openai(b64: str, prompt: str) -> str | None:
    try:
        from openai import OpenAI
        from scanner.config import OPENAI_API_KEY
        if not OPENAI_API_KEY:
            return None
        resp = OpenAI(api_key=OPENAI_API_KEY).chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "low"}},
                {"type": "text",      "text": prompt},
            ]}],
            max_tokens=300,
            temperature=0.4,
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"OpenAI vision failed: {e}")
        return None


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

    # --mode near_buy | top
    mode = "top"
    for i, a in enumerate(args):
        if a == "--mode" and i + 1 < len(args):
            mode = args[i + 1]

    from scanner.utils import is_trading_day

    if save or debug:
        out = pathlib.Path("screenshots")
        out.mkdir(exist_ok=True)

        if not force and not is_trading_day(date.today()):
            logger.info("Không phải ngày giao dịch — bỏ qua (dùng --force để ép)")
            sys.exit(0)

        if tickers:
            stocks = [(t.upper(), _exchange(t.upper()), 0.0) for t in tickers]
        elif mode == "near_buy":
            stocks = _get_near_buy_stocks(top_n)
        else:
            stocks = _get_top_stocks(top_n)

        if not stocks:
            logger.warning("Không có cổ phiếu thỏa tiêu chí")
            sys.exit(0)

        logger.info(f"=== TV Screenshot [{mode}] [{', '.join(t for t,_,_ in stocks)}] ===")
        stock_pairs = [(t, e) for t, e, _ in stocks]
        results = _scrape_all(stock_pairs, debug=debug)

        for ticker, page_label, img in results:
            slug  = "seasonals" if "Mùa vụ" in page_label else "forecast"
            fname = out / f"{ticker}_{slug}.png"
            fname.write_bytes(img)
            logger.info(f"  Saved: {fname} ({len(img)//1024} KB)")

        logger.info(f"=== Xong {len(results)} anh — xem trong screenshots/ ===")
    else:
        run(force=force, top_n=top_n, tickers=tickers, mode=mode)
