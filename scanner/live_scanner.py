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
from scanner.database import bulk_upsert_today, get_top300_thanh_khoan, get_vn100_watchlist, load_all_ohlcv_bulk, load_ohlcv, upsert_ohlcv
from scanner.indicators import calc_bias_norm, calc_supertrend, calc_supertrend_next, get_supertrend_state
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


# ─── Timing helper ────────────────────────────────────────────────────────────

def _wait_until_ict(hour: int, minute: int, abort_after_hour: int | None = None, abort_after_minute: int = 0) -> bool:
    """
    Chờ đến HH:MM ICT. Nếu đã qua abort_after_* thì trả về False (bỏ qua job).
    Nếu đã qua target mà chưa qua abort thì trả về True ngay.
    """
    now = datetime.now(ICT)
    if abort_after_hour is not None and (now.hour, now.minute) >= (abort_after_hour, abort_after_minute):
        logger.warning(
            f"Job chạy quá muộn ({now.strftime('%H:%M')} ICT) — "
            f"bỏ qua (deadline {abort_after_hour:02d}:{abort_after_minute:02d} ICT)"
        )
        return False
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now < target:
        wait_min = (target - now).total_seconds() / 60
        logger.info(f"Chờ đến {hour:02d}:{minute:02d} ICT ({wait_min:.0f} phút nữa)...")
        while datetime.now(ICT) < target:
            time.sleep(30)
        logger.info(f"Đã đến {hour:02d}:{minute:02d} ICT — bắt đầu")
    return True


# ─── Pre-session (8:30 ICT) ───────────────────────────────────────────────────

def run_pre_session() -> None:
    """Báo cáo sáng: load data DB, tính ST, gửi Telegram tóm tắt top 300."""
    if not _wait_until_ict(8, 30, abort_after_hour=9, abort_after_minute=15):
        return  # GitHub Actions delay quá lớn, bỏ qua báo cáo sáng
    _set_api_key()
    logger.info("=== Pre-session scan (8:30 ICT) ===")
    vn100 = get_top300_thanh_khoan(n=300)
    if not vn100:
        logger.error("Watchlist trống")
        return

    logger.info(f"Load OHLCV bulk {len(vn100)} mã từ DB...")
    ticker_data = load_all_ohlcv_bulk(tickers=vn100, days=300)
    logger.info(f"Loaded {len(ticker_data)} mã, tính ST...")
    results: list[dict] = []

    for ticker, df in ticker_data.items():
        try:
            if len(df) < 20:
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
                # price_board trả về VND thực (41500), DB lưu VND/1000 (41.5)
                result[ticker] = {
                    "open":   (float(row["open"] or 0) or close) / 1000,
                    "high":   high   / 1000,
                    "low":    low    / 1000,
                    "close":  close  / 1000,
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
    _wait_until_ict(9, 0)  # chờ đến đúng 9:00 ICT nếu job start sớm
    _set_api_key()
    logger.info(f"=== Session Scanner START (mỗi {interval//60} phút, 9:00-15:15 ICT) ===")

    from scanner.database import get_watchlist
    all_tickers  = get_watchlist()                  # toàn bộ để upsert DB
    top300       = get_top300_thanh_khoan(n=300)    # top 300 để tính ST + alert
    if not top300:
        logger.error("Watchlist trống")
        return

    # ── Load lịch sử OHLCV cho top 300 (dùng để tính ST) ──
    logger.info(f"Load lịch sử OHLCV top 300 từ DB ({len(top300)} mã)...")
    ticker_data = load_all_ohlcv_bulk(tickers=top300, days=300)
    today_ts = pd.Timestamp(date.today())

    hist_cache: dict[str, pd.DataFrame] = {
        t: df[df.index.normalize() < today_ts]
        for t, df in ticker_data.items()
        if not df[df.index.normalize() < today_ts].empty
    }
    logger.info(f"DB full: {len(all_tickers)} mã | ST cache: {len(hist_cache)} mã top 300")

    # ── Init ST state từ lịch sử (1 lần, O(n_bars × n_tickers)) ──
    st_states: dict[str, dict] = {}
    for ticker, hist_df in hist_cache.items():
        try:
            st_states[ticker] = get_supertrend_state(hist_df)
        except Exception:
            pass
    bull = sum(1 for s in st_states.values() if s["trend"] == 1)
    bear = sum(1 for s in st_states.values() if s["trend"] == -1)
    logger.info(f"Init ST state: {len(st_states)} mã | Tang={bull} Giam={bear}")

    send_message(
        f"📡 <b>Session Scanner BẮT ĐẦU</b>\n"
        f"Update DB: {len(all_tickers)} mã | Tín hiệu ST: {len(hist_cache)} mã top 300\n"
        f"Quét mỗi {interval//60} phút | Phiên: 09:00 – 15:15 ICT"
    )

    total_flips   = 0
    scan_count    = 0
    alerted_today: set[str] = set()  # mã đã báo trong phiên → bỏ qua

    while _is_market_open():
        now = datetime.now(ICT)
        scan_count += 1
        logger.info(f"[{now.strftime('%H:%M')}] Scan #{scan_count}...")

        # ── Fetch toàn bộ watchlist (1 API call) ──
        today_bars = _fetch_via_price_board(all_tickers)
        logger.info(f"  price_board: {len(today_bars)}/{len(all_tickers)} bars")

        # ── Bulk upsert 1 lần cho tất cả mã ──
        n_upsert = bulk_upsert_today(today_bars, date.today())
        logger.info(f"  Upsert DB: {n_upsert} rows (1 query)")

        flips: list[dict] = []

        # ── Tính ST incremental O(1)/mã ──
        for ticker, state in st_states.items():
            bar = today_bars.get(ticker)
            if not bar:
                continue
            try:
                new_state = calc_supertrend_next(
                    state,
                    high=bar["high"], low=bar["low"], close=bar["close"],
                )
            except Exception:
                continue

            if ticker not in alerted_today:
                if new_state["buy_signal"]:
                    flips.append({
                        "ticker": ticker, "direction": "buy",
                        "price": bar["close"], "st": new_state["supertrend"],
                    })
                    alerted_today.add(ticker)
                elif new_state["sell_signal"]:
                    flips.append({
                        "ticker": ticker, "direction": "sell",
                        "price": bar["close"], "st": new_state["supertrend"],
                    })
                    alerted_today.add(ticker)

            # Cập nhật state với close mới nhất
            st_states[ticker] = {**new_state, "close": bar["close"]}

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


def _run_session_once() -> None:
    """Chạy đúng 1 cycle để test — không cần trong giờ giao dịch."""
    import time as _time
    t_start = _time.time()

    def _elapsed() -> str:
        return f"{_time.time() - t_start:.1f}s"

    logger.info("=" * 55)
    logger.info("[TEST] SESSION SCANNER — 1 CYCLE")
    logger.info("=" * 55)

    _set_api_key()
    from scanner.database import get_watchlist

    # ── Bước 1: Watchlist ─────────────────────────────────
    t1 = _time.time()
    all_tickers = get_watchlist()
    top300      = get_top300_thanh_khoan(n=300)
    today_ts    = pd.Timestamp(date.today())
    logger.info(f"[1/5] Watchlist: {len(all_tickers)} tong | {len(top300)} top-300 ({_time.time()-t1:.1f}s)")

    # ── Bước 2: Load lịch sử OHLCV ────────────────────────
    t2 = _time.time()
    logger.info(f"[2/5] Load OHLCV 90 ngay cho {len(top300)} ma tu DB...")
    ticker_data = load_all_ohlcv_bulk(tickers=top300, days=300)
    hist_cache  = {
        t: df[df.index.normalize() < today_ts]
        for t, df in ticker_data.items()
        if not df[df.index.normalize() < today_ts].empty
    }
    logger.info(f"[2/5] Loaded: {len(hist_cache)}/{len(top300)} ma co du lich su ({_time.time()-t2:.1f}s)")

    # ── Bước 3: Init prev_trend ────────────────────────────
    t3 = _time.time()
    st_states: dict[str, dict] = {}
    st_ok, st_fail = 0, 0
    for ticker, hist_df in hist_cache.items():
        try:
            st_states[ticker] = get_supertrend_state(hist_df)
            st_ok += 1
        except Exception:
            st_fail += 1
    bull = sum(1 for s in st_states.values() if s["trend"] == 1)
    bear = sum(1 for s in st_states.values() if s["trend"] == -1)
    logger.info(f"[3/5] Init ST: {st_ok} OK / {st_fail} fail | Tang={bull} Giam={bear} ({_time.time()-t3:.1f}s)")

    # ── Bước 4: Fetch price_board + Upsert DB ─────────────
    t4 = _time.time()
    logger.info(f"[4/5] price_board fetch {len(all_tickers)} ma...")
    today_bars = _fetch_via_price_board(all_tickers)
    t4b = _time.time()
    logger.info(f"[4/5] price_board: {len(today_bars)}/{len(all_tickers)} bars ({t4b-t4:.1f}s)")

    n_upsert = bulk_upsert_today(today_bars, date.today())
    logger.info(f"[4/5] Upsert DB: {n_upsert} rows bulk (1 query) ({_time.time()-t4b:.1f}s)")

    # ── Bước 5: Tính ST + detect flip ─────────────────────
    t5 = _time.time()
    flips, st_calc = [], 0
    for ticker, state in st_states.items():
        bar = today_bars.get(ticker)
        if not bar:
            continue
        try:
            new_state = calc_supertrend_next(state, high=bar["high"], low=bar["low"], close=bar["close"])
            st_calc += 1
            if new_state["buy_signal"]:
                flips.append((ticker, "MUA", bar["close"], new_state["supertrend"]))
            elif new_state["sell_signal"]:
                flips.append((ticker, "BAN", bar["close"], new_state["supertrend"]))
        except Exception:
            pass
    logger.info(f"[5/5] Tinh ST incremental: {st_calc} ma | Flip: {len(flips)} ({_time.time()-t5:.1f}s)")

    if flips:
        logger.info("  Flips phat hien:")
        for ticker, direction, price, st in flips:
            logger.info(f"    {direction} {ticker} gia={price:,.0f} ST={st:,.0f}")
    else:
        logger.info("  Khong co flip nao (ngoai gio giao dich — binh thuong)")

    logger.info("=" * 55)
    logger.info(f"[TEST] TONG THOI GIAN: {_elapsed()} — KHONG GUI TELEGRAM")
    logger.info("=" * 55)


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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Chạy 1 cycle dù ngoài giờ giao dịch (để test)",
    )
    args = parser.parse_args()

    if args.mode == "pre":
        run_pre_session()
    elif args.mode == "session":
        if args.force:
            _run_session_once()
        else:
            run_session(interval=args.interval * 60)
    else:
        run_realtime(mode=args.mode, interval=args.interval * 60)
