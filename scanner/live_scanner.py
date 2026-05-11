"""
Live scanner — chạy trong phiên giao dịch (9:00–15:15 ICT).

Modes:
  pre       — 8:30 ICT: báo cáo sáng dùng data DB hôm qua (1 lần, thoát)
  session   — 9:00–15:15 ICT: loop 3 phút, price_board batch, upsert DB, Slack flip alert
  morning   — 9:00–12:00 ICT: loop cũ (giữ tương thích)
  afternoon — 12:00–15:15 ICT: loop cũ (giữ tương thích)

Chạy:
  python -m scanner.live_scanner --mode pre
  python -m scanner.live_scanner --mode session
  python -m scanner.live_scanner --mode session --interval 3
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import pandas as pd
import scanner.utils  # noqa: patch SSL trước
from scanner.data_fetcher import _set_api_key, load_all_from_db
from scanner.database import get_vn100_watchlist, load_ohlcv, upsert_ohlcv
from scanner.indicators import calc_bias_norm, calc_supertrend
from scanner.telegram_bot import send_message
from scanner.utils import fmt_price, logger

# ─── Config ───────────────────────────────────────────────────────────────────
SCAN_INTERVAL_SEC      = 5 * 60      # quét mỗi 5 phút
APPROACH_THRESHOLD_PCT = 1.5         # % cách ST để cảnh báo tiệm cận
FETCH_DELAY_LIVE       = 1.0         # giây chờ giữa request khi dùng fallback per-ticker

ICT = timezone(timedelta(hours=7))   # UTC+7

# Mỗi mode chạy đến giờ này rồi dừng
MODE_UNTIL = {
    "morning":   (12, 0),
    "afternoon": (15, 15),
}


# ─── Pre-session (8:30 ICT) ───────────────────────────────────────────────────

def run_pre_session() -> None:
    """Báo cáo sáng: load data DB, tính ST, gửi Telegram tóm tắt VN100."""
    _set_api_key()
    logger.info("=== Pre-session scan (8:30 ICT) ===")
    vn100 = get_vn100_watchlist()
    if not vn100:
        logger.error("VN100 watchlist trống")
        return

    logger.info(f"Tính SuperTrend cho {len(vn100)} mã từ DB...")
    results: list[dict] = []

    for ticker in vn100:
        try:
            df = load_ohlcv(ticker, days=60)
            if df.empty or len(df) < 20:
                continue
            df = calc_supertrend(df)
            df = calc_bias_norm(df)
            last = df.iloc[-1]
            results.append({
                "ticker":     ticker,
                "close":      float(last["close"]),
                "trend":      int(last["trend"]),
                "supertrend": float(last["supertrend"]),
                "bias_norm":  round(float(last["bias_norm"]), 1),
            })
        except Exception as e:
            logger.debug(f"{ticker}: {e}")

    if not results:
        logger.error("Không có kết quả")
        return

    bull = [r for r in results if r["trend"] == 1]
    bear = [r for r in results if r["trend"] == -1]

    # Mã gần ngưỡng lật nhất (dist < 2%)
    near_flip = []
    for r in results:
        dist = abs(r["close"] - r["supertrend"]) / r["supertrend"] * 100 if r["supertrend"] else 99
        if dist < 2.0:
            near_flip.append({**r, "dist_pct": round(dist, 2)})
    near_flip.sort(key=lambda x: x["dist_pct"])

    today_str = datetime.now(ICT).strftime("%d/%m/%Y")
    lines = [
        f"🌅 <b>Báo cáo đầu phiên VN100 — {today_str}</b>\n",
        f"📊 Xu hướng: 🟢 <b>{len(bull)}</b> tăng | 🔴 <b>{len(bear)}</b> giảm",
    ]

    if near_flip:
        lines.append(f"\n⚠️ <b>Gần ngưỡng lật (&lt;2%) — {len(near_flip)} mã:</b>")
        for r in near_flip[:10]:
            arrow = "⬇️" if r["trend"] == 1 else "⬆️"
            lines.append(
                f"  {arrow} <b>{r['ticker']}</b> | "
                f"Giá {fmt_price(r['close'])} → ST {fmt_price(r['supertrend'])} "
                f"({r['dist_pct']:.1f}%)"
            )

    top_bull = sorted(bull, key=lambda x: -x["bias_norm"])[:5]
    if top_bull:
        lines.append("\n🔥 <b>Top 5 BiasNorm (xu hướng tăng):</b>")
        for r in top_bull:
            lines.append(
                f"  <b>{r['ticker']}</b> | ST {fmt_price(r['supertrend'])} | "
                f"Bias {r['bias_norm']:.0f}/100"
            )

    send_message("\n".join(lines))
    logger.info(f"Pre-session xong: {len(bull)} tăng, {len(bear)} giảm, {len(near_flip)} gần lật")


# ─── Real-time loop (morning / afternoon) ─────────────────────────────────────

def run_realtime(mode: str, interval: int = SCAN_INTERVAL_SEC) -> None:
    """
    Loop quét ST real-time trong phiên.
    Mỗi vòng: fetch bar hôm nay (O/H/L/C/V) → ghép vào lịch sử → tính lại ST.
    """
    _set_api_key()
    until_h, until_m = MODE_UNTIL[mode]
    logger.info(f"=== Live Scanner [{mode.upper()}] START — đến {until_h:02d}:{until_m:02d} ICT ===")

    vn100 = get_vn100_watchlist()
    if not vn100:
        logger.error("VN100 watchlist trống")
        return

    # ── Load lịch sử OHLCV 1 lần duy nhất vào RAM ──
    logger.info(f"Load lịch sử OHLCV từ DB cho {len(vn100)} mã (1 lần)...")
    hist_cache: dict[str, pd.DataFrame] = {}
    today_ts = pd.Timestamp(date.today())

    for ticker in vn100:
        try:
            df = load_ohlcv(ticker, days=60)
            if df.empty or len(df) < 20:
                continue
            # Loại bar hôm nay nếu có trong DB (sẽ fetch real-time)
            df = df[df.index.normalize() < today_ts]
            if not df.empty:
                hist_cache[ticker] = df
        except Exception as e:
            logger.debug(f"{ticker} load_ohlcv: {e}")

    logger.info(f"Cache xong: {len(hist_cache)} mã")
    send_message(
        f"🟡 <b>Live Scanner [{mode.upper()}] BẮT ĐẦU</b>\n"
        f"Theo dõi {len(hist_cache)} mã VN100\n"
        f"Quét mỗi {interval//60} phút — kết thúc {until_h:02d}:{until_m:02d} ICT"
    )

    alerted: set[str] = set()
    scan_count = 0

    while True:
        now = datetime.now(ICT)
        if (now.hour, now.minute) >= (until_h, until_m):
            logger.info(f"Đã đến {until_h:02d}:{until_m:02d} — dừng vòng lặp")
            break

        scan_count += 1
        logger.info(f"[{now.strftime('%H:%M')}] Scan #{scan_count} — {len(hist_cache)} mã...")

        # Fetch bar hôm nay (O/H/L/C/V) song song
        today_bars = _fetch_all_today_bars(list(hist_cache.keys()))

        # Tính ST real-time + phát hiện tín hiệu
        alerts: list[dict] = []
        for ticker, hist_df in hist_cache.items():
            bar = today_bars.get(ticker)
            if bar is None:
                continue
            result = _calc_realtime_st(hist_df, bar)
            if result is None:
                continue
            _collect_alerts(ticker, result, alerted, alerts)

        # Ưu tiên flip trước, tiệm cận sau; cùng loại thì sort theo dist_pct tăng dần
        alerts.sort(key=lambda a: (0 if "cross" in a["type"] else 1, a["dist_pct"]))

        if alerts:
            _send_alerts(alerts)
            for a in alerts:
                alerted.add(f"{a['ticker']}_{a['type']}")

        logger.info(f"  → {len(today_bars)} bar | {len(alerts)} cảnh báo mới")

        elapsed = (datetime.now(ICT) - now).total_seconds()
        sleep_sec = max(0, interval - elapsed)
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    send_message(
        f"🔴 <b>Live Scanner [{mode.upper()}] KẾT THÚC</b> — {until_h:02d}:{until_m:02d} ICT"
    )


# ─── Fetch helpers ────────────────────────────────────────────────────────────

def _fetch_all_today_bars(tickers: list[str]) -> dict[str, dict]:
    """
    Fetch bar hôm nay cho tất cả tickers bằng price_board (1 API call).
    Fallback sang per-ticker nếu price_board thất bại.
    """
    result = _fetch_via_price_board(tickers)
    if result:
        logger.info(f"  price_board: {len(result)}/{len(tickers)} mã")
        return result

    logger.warning("price_board thất bại, fallback per-ticker...")
    return _fetch_via_history(tickers)


def _fetch_via_price_board(tickers: list[str]) -> dict[str, dict]:
    """
    1 API call trả về session O/H/L/C/V cho toàn bộ danh sách.
    price_board trả về MultiIndex columns — flatten rồi map về chuẩn.
    """
    try:
        from vnstock import Trading
        t = Trading(source="VCI")
        df = t.price_board(symbols_list=tickers)
        if df is None or df.empty:
            return {}

        # Flatten MultiIndex: ('match', 'highest') → 'match.highest'
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [
                f"{a}.{b}".lower().strip() if b and str(b) != "nan" else str(a).lower().strip()
                for a, b in df.columns
            ]
        else:
            df.columns = [str(c).lower().strip() for c in df.columns]

        # Map tên cột price_board → chuẩn OHLCV
        FIELD_MAP = {
            "ticker": ["listing.symbol"],
            "open":   ["match.open_price"],
            "high":   ["match.highest"],
            "low":    ["match.lowest"],
            "close":  ["match.match_price"],
            "volume": ["match.accumulated_volume"],
        }
        col_map: dict[str, str] = {}
        for target, candidates in FIELD_MAP.items():
            for c in candidates:
                if c in df.columns:
                    col_map[c] = target
                    break

        df = df.rename(columns=col_map)
        required = {"ticker", "open", "high", "low", "close", "volume"}
        if not required.issubset(df.columns):
            logger.warning(f"price_board thiếu cột: {required - set(df.columns)}")
            return {}

        result: dict[str, dict] = {}
        no_hl = 0
        for _, row in df.iterrows():
            ticker = str(row["ticker"]).strip().upper()
            try:
                close = float(row["close"] or 0)
                if close <= 0:
                    continue
                high = float(row["high"] or 0) or close
                low  = float(row["low"]  or 0) or close
                if high < low:          # data lỗi, bỏ qua
                    continue
                if high == low == close:
                    no_hl += 1          # chưa có H/L thực (ATO hoặc 1 lệnh đầu)
                result[ticker] = {
                    "open":   float(row["open"] or 0) or close,
                    "high":   high,
                    "low":    low,
                    "close":  close,
                    "volume": float(row["volume"] or 0),
                }
            except (TypeError, ValueError):
                continue
        if no_hl:
            logger.debug(f"price_board: {no_hl} mã H=L=C (chưa có session H/L)")
        return result
    except Exception as e:
        logger.debug(f"price_board error: {e}")
        return {}


def _fetch_via_history(tickers: list[str]) -> dict[str, dict]:
    """Fallback: per-ticker Quote.history() với delay 1s/req."""
    today = date.today().strftime("%Y-%m-%d")
    result: dict[str, dict] = {}
    for ticker in tickers:
        try:
            from vnstock.api.quote import Quote
            for source in ["VCI", "TCBS"]:
                try:
                    df = Quote(symbol=ticker, source=source).history(
                        start=today, end=today, interval="1D"
                    )
                    if df is None or df.empty:
                        continue
                    df.columns = [c.lower() for c in df.columns]
                    row = df.iloc[-1]
                    close = float(row.get("close", row.get("c", 0)))
                    if close > 0:
                        result[ticker] = {
                            "open":   float(row.get("open",   close)),
                            "high":   float(row.get("high",   close)),
                            "low":    float(row.get("low",    close)),
                            "close":  close,
                            "volume": float(row.get("volume", 0)),
                        }
                        break
                except Exception:
                    continue
        except Exception:
            pass
        time.sleep(FETCH_DELAY_LIVE)
    return result


# ─── ST calculation ───────────────────────────────────────────────────────────

def _calc_realtime_st(hist_df: pd.DataFrame, today_bar: dict) -> Optional[dict]:
    """Ghép bar hôm nay vào lịch sử, tính lại SuperTrend, trả về kết quả bar cuối."""
    try:
        today_row = pd.DataFrame(
            [today_bar],
            index=[pd.Timestamp(date.today())],
        )
        df = pd.concat([hist_df, today_row])
        df = calc_supertrend(df)
        last = df.iloc[-1]
        return {
            "close":      float(last["close"]),
            "trend":      int(last["trend"]),
            "supertrend": float(last["supertrend"]),
            "buy_signal": bool(last["buy_signal"]),
            "sell_signal": bool(last["sell_signal"]),
        }
    except Exception:
        return None


# ─── Alert helpers ────────────────────────────────────────────────────────────

def _collect_alerts(
    ticker: str,
    result: dict,
    already_alerted: set[str],
    alerts: list[dict],
) -> None:
    """Kiểm tra flip / tiệm cận, thêm vào alerts nếu chưa gửi hôm nay."""
    price = result["close"]
    st    = result["supertrend"]
    trend = result["trend"]
    dist_pct = abs(price - st) / st * 100 if st else 0

    if result["buy_signal"] and f"{ticker}_cross_buy" not in already_alerted:
        alerts.append({
            "ticker": ticker, "type": "cross_buy",
            "price": price, "st": st, "dist_pct": dist_pct,
            "msg": "🟢 <b>LẬT LÊN (MUA)</b> — Giá vượt qua SuperTrend",
        })

    elif result["sell_signal"] and f"{ticker}_cross_sell" not in already_alerted:
        alerts.append({
            "ticker": ticker, "type": "cross_sell",
            "price": price, "st": st, "dist_pct": dist_pct,
            "msg": "🔴 <b>LẬT XUỐNG (BÁN)</b> — Giá xuyên qua SuperTrend",
        })

    elif dist_pct <= APPROACH_THRESHOLD_PCT:
        direction = "xuong" if trend == 1 else "len"
        key = f"{ticker}_approach_{direction}"
        if key not in already_alerted:
            arrow = "⬇️" if trend == 1 else "⬆️"
            alerts.append({
                "ticker": ticker, "type": f"approach_{direction}",
                "price": price, "st": st, "dist_pct": dist_pct,
                "msg": f"⚠️ <b>TIỆM CẬN lật {arrow}</b>",
            })


def _send_alerts(alerts: list[dict]) -> None:
    """Gửi tối đa 10 alert một đợt."""
    if len(alerts) > 10:
        send_message(f"⚠️ <b>{len(alerts)} tín hiệu trong phiên</b> — Hiển thị top 10:")
        alerts = alerts[:10]

    for a in alerts:
        text = (
            f"{a['msg']}\n"
            f"<b>{a['ticker']}</b> | Giá: <b>{fmt_price(a['price'])}</b>\n"
            f"SuperTrend: {fmt_price(a['st'])} | Cách: {a['dist_pct']:.2f}%"
        )
        send_message(text)
        time.sleep(0.3)


def _is_market_open() -> bool:
    now = datetime.now(ICT)
    if now.weekday() >= 5:
        return False
    t = (now.hour, now.minute)
    return (9, 0) <= t < (15, 15)


# ─── Session scanner (main mode) ─────────────────────────────────────────────

def run_session(interval: int = 180) -> None:
    """
    Loop chính trong phiên 9:00–15:15 ICT.
    - Fetch toàn bộ watchlist bằng price_board (1 API call, ~0.5s) → upsert DB
    - Tính SuperTrend real-time chỉ cho VN100 (có đủ 60 ngày lịch sử)
    - Gửi Telegram khi có mã VN100 lật trend
    """
    _set_api_key()
    logger.info(f"=== Session Scanner START (mỗi {interval//60} phút, 9:00-15:15 ICT) ===")

    from scanner.database import get_watchlist
    all_tickers = get_watchlist()   # toàn bộ watchlist để upsert DB
    vn100       = get_vn100_watchlist()
    if not vn100:
        logger.error("VN100 watchlist trống")
        return

    # ── Load lịch sử OHLCV cho VN100 (dùng để tính ST) ──
    logger.info(f"Load lịch sử OHLCV VN100 từ DB ({len(vn100)} mã)...")
    ticker_data = load_all_from_db(tickers=vn100, days=60)
    today_ts = pd.Timestamp(date.today())

    hist_cache: dict[str, pd.DataFrame] = {
        t: df[df.index.normalize() < today_ts]
        for t, df in ticker_data.items()
        if not df[df.index.normalize() < today_ts].empty
    }
    logger.info(f"DB full: {len(all_tickers)} mã | ST cache: {len(hist_cache)} mã VN100")

    # ── Init prev_trend ──
    prev_trend: dict[str, int] = {}
    for ticker, hist_df in hist_cache.items():
        try:
            df_st = calc_supertrend(hist_df)
            prev_trend[ticker] = int(df_st.iloc[-1]["trend"])
        except Exception:
            pass

    send_message(
        f"📡 <b>Session Scanner BẮT ĐẦU</b>\n"
        f"Update DB: {len(all_tickers)} mã | Tín hiệu ST: {len(hist_cache)} mã VN100\n"
        f"Quét mỗi {interval//60} phút | Phiên: 09:00 – 15:15 ICT"
    )

    total_flips = 0
    scan_count  = 0

    while _is_market_open():
        now = datetime.now(ICT)
        scan_count += 1
        logger.info(f"[{now.strftime('%H:%M')}] Scan #{scan_count}...")

        # ── Fetch toàn bộ watchlist (1 API call) ──
        today_bars = _fetch_via_price_board(all_tickers)
        logger.info(f"  price_board: {len(today_bars)}/{len(all_tickers)} bars")

        # ── Upsert DB cho tất cả mã có data ──
        for ticker, bar in today_bars.items():
            try:
                upsert_ohlcv(ticker, pd.DataFrame([bar], index=[today_ts]))
            except Exception as e:
                logger.debug(f"upsert {ticker}: {e}")

        flips: list[dict] = []

        # ── Tính ST chỉ cho VN100 ──
        for ticker, hist_df in hist_cache.items():
            bar = today_bars.get(ticker)
            if not bar:
                continue

            # Tính ST real-time
            result = _calc_realtime_st(hist_df, bar)
            if result is None:
                continue

            # Detect flip so với trend trước đó
            prev = prev_trend.get(ticker)
            if prev is not None:
                if result["buy_signal"] and prev != 1:
                    flips.append({
                        "ticker": ticker, "direction": "buy",
                        "price": result["close"], "st": result["supertrend"],
                    })
                elif result["sell_signal"] and prev != -1:
                    flips.append({
                        "ticker": ticker, "direction": "sell",
                        "price": result["close"], "st": result["supertrend"],
                    })

            prev_trend[ticker] = result["trend"]

        # ── Gửi Telegram cho từng flip ──
        for flip in flips:
            emoji = "🟢" if flip["direction"] == "buy" else "🔴"
            label = "LẬT TĂNG — XEM XÉT MUA" if flip["direction"] == "buy" else "LẬT GIẢM — XEM XÉT BÁN"
            send_message(
                f"{emoji} <b>{flip['ticker']}</b> | {label}\n"
                f"Giá: <b>{fmt_price(flip['price'])}</b> | ST: {fmt_price(flip['st'])}\n"
                f"Thời gian: {now.strftime('%H:%M')} ICT"
            )
            logger.info(
                f"  FLIP {flip['direction'].upper()}: {flip['ticker']} "
                f"giá={flip['price']:,.0f} ST={flip['st']:,.0f}"
            )

        total_flips += len(flips)
        logger.info(f"  Upsert: {len(today_bars)} | Flip: {len(flips)} | Tổng: {total_flips}")

        elapsed = (datetime.now(ICT) - now).total_seconds()
        sleep_sec = max(0, interval - elapsed)
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    send_message(
        f"🔔 <b>Session Scanner KẾT THÚC</b> (15:15 ICT)\n"
        f"Tổng tín hiệu trong phiên: {total_flips}"
    )
    logger.info(f"=== Session Scanner KẾT THÚC — {total_flips} flips trong phiên ===")


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="VN100 Live SuperTrend Scanner")
    parser.add_argument(
        "--mode",
        choices=["pre", "session", "morning", "afternoon"],
        required=True,
        help="pre=báo cáo sáng | session=9:00-15:15 (main) | morning/afternoon=loop cũ",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=3,
        help="Phút giữa mỗi lần quét (default: 3)",
    )
    args = parser.parse_args()

    if args.mode == "pre":
        run_pre_session()
    elif args.mode == "session":
        run_session(interval=args.interval * 60)
    else:
        run_realtime(mode=args.mode, interval=args.interval * 60)
