"""
Live scanner — chạy trong phiên giao dịch (9:00 - 15:15 ICT).
Mỗi N phút: fetch giá hiện tại → so sánh vs SuperTrend level hôm qua.
Alert Telegram khi giá tiệm cận hoặc vượt ngưỡng lật.

Chạy: python -m scanner.live_scanner
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import pandas as pd
import scanner.utils  # noqa: patch SSL trước
from scanner.config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from scanner.database import get_watchlist, load_ohlcv, load_scan_results, load_scan_dates
from scanner.indicators import calc_supertrend
from scanner.telegram_bot import send_message
from scanner.utils import fmt_price, logger

# ─── Config ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC = 5 * 60        # quét mỗi 5 phút
APPROACH_THRESHOLD_PCT = 1.5      # cảnh báo khi giá cách ST < 1.5%
MAX_WORKERS_LIVE = 3              # số ticker fetch song song

ICT = timezone(timedelta(hours=7))  # UTC+7

MARKET_OPEN  = (9,  0)
MARKET_CLOSE = (15, 15)


# ─── Main loop ────────────────────────────────────────────────────────────────

def run(interval: int = SCAN_INTERVAL_SEC) -> None:
    logger.info("=== Live Scanner START ===")
    logger.info(f"Quét mỗi {interval//60} phút | Ngưỡng cảnh báo: {APPROACH_THRESHOLD_PCT}%")

    # Load SuperTrend levels từ scan_results hôm qua / gần nhất
    st_levels = _load_supertrend_levels()
    if not st_levels:
        logger.error("Không có SuperTrend levels. Chạy scanner.updater trước.")
        return

    logger.info(f"Loaded {len(st_levels)} SuperTrend levels")
    send_message(
        f"🟡 <b>Live Scanner BẮT ĐẦU</b>\n"
        f"Theo dõi {len(st_levels)} mã | Cảnh báo khi giá cách ST &lt;{APPROACH_THRESHOLD_PCT}%\n"
        f"Quét mỗi {interval//60} phút | Kết thúc lúc 15:15 ICT"
    )

    alerted: set[str] = set()  # tránh gửi alert trùng lặp trong ngày

    while _is_market_open():
        now = datetime.now(ICT).strftime("%H:%M")
        logger.info(f"[{now}] Đang quét {len(st_levels)} mã...")

        prices = _fetch_all_prices(list(st_levels.keys()))
        if not prices:
            logger.warning("Không fetch được giá nào")
            time.sleep(interval)
            continue

        alerts = _check_signals(st_levels, prices, alerted)
        if alerts:
            _send_alerts(alerts)
            for a in alerts:
                alerted.add(f"{a['ticker']}_{a['type']}")

        logger.info(f"  → {len(prices)} giá | {len(alerts)} cảnh báo mới")
        time.sleep(interval)

    logger.info("Thị trường đã đóng cửa. Live Scanner kết thúc.")
    send_message("🔴 <b>Live Scanner KẾT THÚC</b> — Thị trường đóng cửa 15:15 ICT")


# ─── Logic ────────────────────────────────────────────────────────────────────

def _load_supertrend_levels() -> dict[str, dict]:
    """
    Load SuperTrend level từ scan_results (ngày gần nhất trong DB).
    Returns {ticker: {trend, supertrend, close, bias_norm}}
    """
    try:
        dates = load_scan_dates()
        if not dates:
            return {}
        latest = dates[0]
        df = load_scan_results(latest)
        if df.empty:
            return {}
        result = {}
        for _, row in df.iterrows():
            result[row["ticker"]] = {
                "trend":      int(row.get("trend", 1)),
                "supertrend": float(row.get("supertrend", 0)),
                "close":      float(row.get("close", 0)),
                "bias_norm":  float(row.get("bias_norm", 50)),
            }
        return result
    except Exception:
        # Fallback: tính lại từ OHLCV DB
        return _compute_levels_from_db()


def _compute_levels_from_db() -> dict[str, dict]:
    """Tính SuperTrend trực tiếp từ OHLCV trong DB (fallback)."""
    tickers = get_watchlist()
    result = {}
    for ticker in tickers:
        try:
            df = load_ohlcv(ticker, days=60)
            if df.empty or len(df) < 20:
                continue
            df = calc_supertrend(df)
            last = df.iloc[-1]
            result[ticker] = {
                "trend":      int(last["trend"]),
                "supertrend": float(last["supertrend"]),
                "close":      float(last["close"]),
                "bias_norm":  50.0,
            }
        except Exception:
            pass
    return result


def _fetch_all_prices(tickers: list[str]) -> dict[str, float]:
    """
    Fetch giá hiện tại (last price) cho tất cả ticker.
    Dùng Quote.history() với ngày hôm nay để lấy bar đang hình thành.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    prices: dict[str, float] = {}
    today = date.today().strftime("%Y-%m-%d")

    def _fetch_one(ticker: str) -> tuple[str, Optional[float]]:
        try:
            from vnstock.api.quote import Quote
            for source in ["VCI", "KBS"]:
                try:
                    q = Quote(symbol=ticker, source=source)
                    df = q.history(start=today, end=today, interval="1D")
                    if df is not None and not df.empty:
                        close_col = next(
                            (c for c in ["close", "Close", "price"] if c in df.columns), None
                        )
                        if close_col:
                            return ticker, float(df[close_col].iloc[-1])
                except Exception:
                    continue
        except Exception:
            pass
        return ticker, None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS_LIVE) as exe:
        futures = {exe.submit(_fetch_one, t): t for t in tickers}
        for f in as_completed(futures):
            ticker, price = f.result()
            if price is not None:
                prices[ticker] = price

    return prices


def _check_signals(
    st_levels: dict[str, dict],
    prices: dict[str, float],
    already_alerted: set[str],
) -> list[dict]:
    """
    So sánh giá hiện tại vs SuperTrend level.
    Trả về danh sách alert chưa gửi.
    """
    alerts = []

    for ticker, info in st_levels.items():
        price = prices.get(ticker)
        if not price or not info["supertrend"]:
            continue

        st = info["supertrend"]
        trend = info["trend"]
        dist_pct = abs(price - st) / st * 100

        # Đã lật (cross)
        if trend == 1 and price < st:
            alert_key = f"{ticker}_cross_sell"
            if alert_key not in already_alerted:
                alerts.append({
                    "ticker": ticker, "type": "cross_sell",
                    "price": price, "st": st, "dist_pct": dist_pct,
                    "bias_norm": info["bias_norm"],
                    "msg": "🔴 <b>ĐÃ LẬT XUỐNG</b> — Giá xuyên qua SuperTrend",
                })

        elif trend == -1 and price > st:
            alert_key = f"{ticker}_cross_buy"
            if alert_key not in already_alerted:
                alerts.append({
                    "ticker": ticker, "type": "cross_buy",
                    "price": price, "st": st, "dist_pct": dist_pct,
                    "bias_norm": info["bias_norm"],
                    "msg": "🟢 <b>ĐÃ LẬT LÊN</b> — Giá xuyên qua SuperTrend",
                })

        # Tiệm cận (approach)
        elif dist_pct <= APPROACH_THRESHOLD_PCT:
            direction = "xuống" if trend == 1 else "lên"
            alert_key = f"{ticker}_approach_{direction}"
            if alert_key not in already_alerted:
                alerts.append({
                    "ticker": ticker, "type": f"approach_{direction}",
                    "price": price, "st": st, "dist_pct": dist_pct,
                    "bias_norm": info["bias_norm"],
                    "msg": f"⚠️ <b>TIỆM CẬN ngưỡng lật {direction.upper()}</b>",
                })

    # Ưu tiên: cross trước, approach sau; sort theo dist_pct
    alerts.sort(key=lambda a: (0 if "cross" in a["type"] else 1, a["dist_pct"]))
    return alerts


def _send_alerts(alerts: list[dict]) -> None:
    """Gửi tối đa 10 alert, gộp nhóm nếu nhiều."""
    if len(alerts) > 10:
        summary = f"⚠️ <b>{len(alerts)} tín hiệu trong phiên</b> — Hiển thị top 10:\n"
        send_message(summary)
        alerts = alerts[:10]

    for a in alerts:
        text = (
            f"{a['msg']}\n"
            f"<b>{a['ticker']}</b> | Giá: <b>{fmt_price(a['price'])}</b>\n"
            f"SuperTrend: {fmt_price(a['st'])} | Cách: {a['dist_pct']:.2f}%\n"
            f"BiasNorm: {a['bias_norm']:.0f}/100"
        )
        send_message(text)
        time.sleep(0.5)


def _is_market_open() -> bool:
    now = datetime.now(ICT)
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t < MARKET_CLOSE


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=5,
                        help="Phút giữa mỗi lần quét (default: 5)")
    parser.add_argument("--threshold", type=float, default=APPROACH_THRESHOLD_PCT,
                        help="Ngưỡng cảnh báo %% cách SuperTrend (default: 1.5)")
    parser.add_argument("--force", action="store_true",
                        help="Chạy ngay kể cả ngoài giờ giao dịch (để test)")
    args = parser.parse_args()

    APPROACH_THRESHOLD_PCT = args.threshold

    if args.force:
        # Test mode: chạy 1 lần rồi thoát
        levels = _load_supertrend_levels()
        prices = _fetch_all_prices(list(levels.keys())[:20])
        alerts = _check_signals(levels, prices, set())
        print(f"Levels: {len(levels)} | Prices: {len(prices)} | Alerts: {len(alerts)}")
        for a in alerts[:5]:
            print(a)
    else:
        run(interval=args.interval * 60)
