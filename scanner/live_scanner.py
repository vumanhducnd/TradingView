"""
Live scanner — chạy trong phiên giao dịch (9:00–15:15 ICT).

Modes:
  pre       — 7:00 ICT: báo cáo sáng dùng data DB hôm qua (1 lần, thoát)
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
from scanner.data_fetcher import _set_api_key, _snap_to_tick, load_all_from_db
from scanner.database import bulk_upsert_today, get_top300_thanh_khoan, get_vn100_watchlist, load_all_ohlcv_bulk, load_ohlcv, upsert_ohlcv
from scanner.indicators import calc_bias_norm, calc_supertrend, calc_supertrend_next, get_supertrend_state
from scanner.telegram_bot import send_message, send_to_user
from scanner.utils import fmt_date, fmt_price, logger, tk_label, tv_link

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


# ─── Pre-session (7:00 ICT) ───────────────────────────────────────────────────

_NEAR_THRESHOLD_PCT = 1.5   # % cách ST để coi là "gần điểm mua/bán"
_TOP_N_PRE          = 10    # số mã hiển thị mỗi nhóm


def _fmt_pre_row(r: dict) -> str:
    """Format 1 dòng mã: SÀNG:MÃ | Giá | TK"""
    exch   = r.get("exchange", "")
    prefix = f"{exch}:" if exch else ""
    tk_ty  = r["turnover"] / 1e9 if r.get("turnover") else 0.0
    tk_str = f"{tk_ty:.1f} tỷ ({tk_label(tk_ty)})" if tk_ty > 0 else "–"
    return (
        f"  <b>{prefix}{tv_link(r['ticker'], exch)}</b> | "
        f"Giá {fmt_price(r['close'])} | "
        f"TK {tk_str}"
    )


def _build_pre_report(results: list[dict], style_label: str, today_str: str,
                      ai_text: str = "") -> tuple[str, list[dict], list[dict]]:
    """Tạo 1 message duy nhất: header + AI + gần mua + gần bán."""
    bull = [r for r in results if r["trend"] == 1]
    bear = [r for r in results if r["trend"] == -1]

    near_buy = []
    for r in bear:
        st = r["supertrend"]
        if not st:
            continue
        dist = (st - r["close"]) / st * 100
        if 0 <= dist <= _NEAR_THRESHOLD_PCT:
            near_buy.append({**r, "dist_pct": -dist})
    near_buy.sort(key=lambda x: -x.get("turnover", 0))

    near_sell = []
    for r in bull:
        st = r["supertrend"]
        if not st:
            continue
        dist = (r["close"] - st) / r["close"] * 100
        if 0 <= dist <= _NEAR_THRESHOLD_PCT:
            near_sell.append({**r, "dist_pct": dist})
    near_sell.sort(key=lambda x: -x.get("turnover", 0))

    lines = [f"🌅 <b>Báo cáo sáng 7:00 — Đầu tư {style_label} — {today_str}</b>"]

    from scanner.market_calendar import get_market_events, format_events_for_telegram
    events = get_market_events()
    event_block = format_events_for_telegram(events)
    if event_block:
        lines.append(event_block)
    if ai_text:
        lines.append(f"\n{ai_text}")

    if near_buy:
        lines.append(f"\n🚀 <b>Gần điểm MUA:</b>")
        for r in near_buy[:_TOP_N_PRE]:
            lines.append(_fmt_pre_row(r))

    if near_sell:
        lines.append(f"\n🔻 <b>Gần điểm BÁN:</b>")
        for r in near_sell[:_TOP_N_PRE]:
            lines.append(_fmt_pre_row(r))

    if not near_buy and not near_sell:
        lines.append("\n✅ Không có mã nào gần ngưỡng lật trong phiên hôm nay.")

    return "\n".join(lines), near_buy, near_sell


def run_pre_session(force: bool = False) -> None:
    """Báo cáo 7:00 ICT: tính ST riêng dài hạn & ngắn hạn, gửi 2 bot."""
    if not force and not _wait_until_ict(7, 0, abort_after_hour=8, abort_after_minute=30):
        return
    _set_api_key()
    logger.info("=== Pre-session scan (7:00 ICT) ===")

    tickers = get_top300_thanh_khoan(n=300)
    if not tickers:
        logger.error("Watchlist trống")
        return

    logger.info(f"Load OHLCV {len(tickers)} mã từ DB...")
    ticker_data = load_all_ohlcv_bulk(tickers=tickers, days=300)

    # Load exchange info từ watchlist
    try:
        from scanner.database import db_cursor
        with db_cursor(commit=False) as cur:
            cur.execute("SELECT ticker, exchange FROM watchlist WHERE ticker = ANY(%s)", (tickers,))
            exchange_map = {r["ticker"]: r["exchange"] for r in cur.fetchall()}
    except Exception:
        exchange_map = {}

    # Load avg_turnover_20d làm TK đại diện
    from scanner.database import load_avg_turnover
    tk_map = load_avg_turnover(tickers)

    today_str = datetime.now(ICT).strftime("%d/%m/%Y")

    # Fetch sự kiện quốc tế 1 lần duy nhất, dùng chung cho cả long và short
    from scanner.market_calendar import fetch_global_events
    global_block = fetch_global_events()

    for style, style_label, bot_style in [
        ("long",  "Dài hạn",  "long"),
        ("short", "Ngắn hạn", "short"),
    ]:
        results: list[dict] = []
        for ticker, df in ticker_data.items():
            try:
                if len(df) < 20:
                    continue
                df_st = calc_supertrend(df, style=style)
                last = df_st.iloc[-1]
                avg_tk = tk_map.get(ticker, 0)
                results.append({
                    "ticker":     ticker,
                    "exchange":   exchange_map.get(ticker, ""),
                    "close":      float(last["close"]),
                    "trend":      int(last["trend"]),
                    "supertrend": float(last["supertrend"]),
                    "turnover":   avg_tk * 1000,   # → VND thực để hiển thị tỷ
                })
            except Exception as e:
                logger.debug(f"{ticker} [{style}]: {e}")

        bull_cnt = sum(1 for r in results if r["trend"] == 1)
        bear_cnt = sum(1 for r in results if r["trend"] == -1)

        # Lấy near_buy/near_sell trước để truyền vào AI
        _, near_buy_tmp, near_sell_tmp = _build_pre_report(results, style_label, today_str)

        # AI nhận định trước phiên
        ai_text = ""
        try:
            from scanner.ai_analyst import generate_pre_session_ai
            ai_text = generate_pre_session_ai(
                n_bull=bull_cnt, n_bear=bear_cnt, n_total=len(results),
                near_buy=near_buy_tmp, near_sell=near_sell_tmp,
                style_label=style_label,
            )
        except Exception as e:
            logger.warning(f"AI pre-session failed: {e}")

        msg, _, _ = _build_pre_report(results, style_label, today_str, ai_text=ai_text)
        send_message(msg, style=bot_style)
        logger.info(f"Pre-session [{style}]: {len(results)} mã, gửi bot {bot_style}")

    # Gửi sự kiện quốc tế riêng sau cả 2 báo cáo
    if global_block:
        for bot_style in ("long", "short"):
            send_message(global_block, style=bot_style)

    logger.info("Pre-session xong")


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
                    "open":   _snap_to_tick((float(row["open"] or 0) or close) / 1000),
                    "high":   _snap_to_tick(high   / 1000),
                    "low":    _snap_to_tick(low    / 1000),
                    "close":  _snap_to_tick(close  / 1000),
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
                            "open":   _snap_to_tick(float(row.get("open",   close))),
                            "high":   _snap_to_tick(float(row.get("high",   close))),
                            "low":    _snap_to_tick(float(row.get("low",    close))),
                            "close":  _snap_to_tick(close),
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

def _save_signal_to_db(ticker: str, signal_type: str, style: str,
                       price: float | None = None, st: float | None = None) -> None:
    """Ghi flip vào signals DB để phiên chiều không re-fire khi load lại."""
    try:
        from scanner.database import db_cursor
        with db_cursor(commit=True) as cur:
            cur.execute(
                """INSERT INTO signals (ticker, signal_date, signal_type, style, signal_price, signal_st)
                   VALUES (%s, CURRENT_DATE, %s, %s, %s, %s)
                   ON CONFLICT ON CONSTRAINT signals_ticker_date_type_style_key DO NOTHING""",
                (ticker, signal_type, style, price, st),
            )
    except Exception as e:
        logger.debug(f"signals insert failed {ticker}: {e}")


def _save_rut_chan_report(
    alerted_signals: dict[str, dict],
    st_states_long: dict[str, dict],
    st_states_short: dict[str, dict],
) -> None:
    """Đánh dấu is_fakeout=TRUE trong bảng signals cho các mã rút chân."""
    if not alerted_signals:
        return

    fakeout_keys: list[tuple[str, str]] = []   # (ticker, style)
    for alert_key, sig in alerted_signals.items():
        ticker, style_key = alert_key.rsplit("_", 1)
        st_states = st_states_long if style_key == "long" else st_states_short
        state = st_states.get(ticker)
        if state is None:
            continue
        direction  = sig["direction"]
        final_trend = state["trend"]
        if (direction == "buy" and final_trend == -1) or (direction == "sell" and final_trend == 1):
            fakeout_keys.append((ticker, style_key))

    if not fakeout_keys:
        logger.info("Khong co ma rut chan hom nay")
        return

    try:
        from scanner.database import db_cursor
        with db_cursor(commit=True) as cur:
            cur.executemany(
                """UPDATE signals SET is_fakeout = TRUE
                   WHERE ticker = %s AND style = %s AND signal_date = CURRENT_DATE""",
                fakeout_keys,
            )
        logger.info(f"Rut chan: danh dau {len(fakeout_keys)} ma trong DB")
    except Exception as e:
        logger.warning(f"UPDATE is_fakeout that bai: {e}")


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

    from scanner.database import load_exchange_map
    exch_map = load_exchange_map([a["ticker"] for a in alerts])

    for a in alerts:
        ticker_link = tv_link(a["ticker"], exch_map.get(a["ticker"], ""))
        text = (
            f"{a['msg']}\n"
            f"<b>{ticker_link}</b> | Giá: <b>{fmt_price(a['price'])}</b>\n"
            f"SuperTrend: {fmt_price(a['st'])} | Cách: {a['dist_pct']:.2f}%"
        )
        send_message(text)
        time.sleep(0.3)


_SESSION_HOURS = {
    "morning":   ((9, 0),  (11, 30)),   # 9:00 – 11:30
    "afternoon": ((13, 0), (14, 45)),   # 13:00 – 14:45
    "full":      ((9, 0),  (14, 45)),   # cả ngày (backup)
}

def _is_market_open(session: str = "full") -> bool:
    now = datetime.now(ICT)
    if now.weekday() >= 5:
        return False
    start, end = _SESSION_HOURS.get(session, _SESSION_HOURS["full"])
    t = (now.hour, now.minute)
    return start <= t < end


# ─── Session scanner (main mode) ─────────────────────────────────────────────

def run_session(interval: int = 180, session: str = "full") -> None:
    """
    Loop trong phiên giao dịch.
    session: 'morning' (9:00-11:30) | 'afternoon' (13:00-14:45) | 'full' (cả ngày)
    """
    label = {"morning": "Sáng (9:00-11:30)", "afternoon": "Chiều (13:00-14:45)", "full": "Cả ngày"}.get(session, session)
    _set_api_key()
    logger.info(f"=== Session Scanner [{label}] START ===")

    from scanner.database import get_watchlist, _ensure_signals_schema
    _ensure_signals_schema()   # đảm bảo các cột mới (signal_price, signal_st, is_fakeout) tồn tại
    all_tickers  = get_watchlist()                  # toàn bộ để upsert DB
    top300       = get_top300_thanh_khoan(n=300)    # top 300 để tính ST + alert
    if not top300:
        logger.error("Watchlist trống")
        return

    # ── Load exchange map cho top 300 ──
    try:
        from scanner.database import db_cursor
        with db_cursor(commit=False) as cur:
            cur.execute("SELECT ticker, exchange FROM watchlist WHERE ticker = ANY(%s)", (top300,))
            exchange_map = {r["ticker"]: r["exchange"] for r in cur.fetchall()}
    except Exception:
        exchange_map = {}

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

    # ── Init ST state cho cả LONG và SHORT ──
    st_states_long:  dict[str, dict] = {}
    st_states_short: dict[str, dict] = {}
    for ticker, hist_df in hist_cache.items():
        try:
            st_states_long[ticker]  = get_supertrend_state(hist_df, style="long")
            st_states_short[ticker] = get_supertrend_state(hist_df, style="short")
        except Exception:
            pass
    bull_l = sum(1 for s in st_states_long.values()  if s["trend"] == 1)
    bull_s = sum(1 for s in st_states_short.values() if s["trend"] == 1)
    logger.info(f"Init ST: {len(st_states_long)} mã | DH tang={bull_l} | NH tang={bull_s}")

    _wd = datetime.now(ICT).weekday()
    _open_lines = {
        "morning": {
            0: "Tuần mới bắt đầu rồi. Cà phê cầm tay, mắt dán bảng điện, chiến thôi.",
            1: "Vào phiên sáng rồi anh em. Hôm qua thế nào thì hôm nay làm lại, theo dõi nhé.",
            2: "Nửa tuần rồi đấy. Phiên sáng mở cửa, cố lên nào.",
            3: "Sắp cuối tuần rồi, còn hai phiên nữa thôi. Tập trung vào đi.",
            4: "Phiên sáng cuối tuần đây. Chốt đẹp hôm nay là nghỉ ngơi thoải mái.",
        },
        "afternoon": {
            0: "Nghỉ trưa đủ chưa? Vào phiên chiều thôi, tuần mới còn dài.",
            1: "Cơm xong rồi, phiên chiều mở cửa. Vào tiếp đi anh em.",
            2: "Phiên chiều giữa tuần đây. Có tín hiệu đẹp là quá ổn.",
            3: "Phiên chiều cuối tuần gần kề. Tranh thủ nếu có cơ hội.",
            4: "Phiên chiều cuối cùng của tuần rồi. Chốt xong tắt máy nghỉ ngơi.",
        },
    }
    _start_text = _open_lines.get(session, {}).get(_wd, "Phiên giao dịch bắt đầu. Có tín hiệu sẽ báo ngay.")
    send_message(_start_text, style="long")
    send_message(_start_text, style="short")

    total_flips = 0
    scan_count  = 0

    # Load từ DB: những mã đã báo hôm nay (tránh báo lại khi phiên chiều start)
    def _load_alerted_from_db() -> set[str]:
        try:
            from scanner.database import db_cursor
            with db_cursor(commit=False) as cur:
                cur.execute(
                    """SELECT ticker, style FROM signals
                       WHERE signal_date = CURRENT_DATE""",
                )
                return {f"{r['ticker']}_{r['style']}" for r in cur.fetchall()}
        except Exception:
            return set()

    alerted_today: set[str] = _load_alerted_from_db()
    logger.info(f"Loaded {len(alerted_today)} ma da bao hom nay tu DB")
    # direction/price/time của từng flip gửi trong session này (cho báo cáo rút chân)
    alerted_signals: dict[str, dict] = {}

    while _is_market_open(session):
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

        # ── So sánh giá hiện tại với SuperTrend daily (không dùng incremental) ──
        # SuperTrend là chỉ báo daily — state giữ nguyên từ hôm qua, không update intraday.
        # Nếu feed nhiều bar intraday vào calc_supertrend_next, ATR Wilder bị decay
        # (nhân 6/7 mỗi cycle) khiến dn/up trôi xa khỏi giá trị thực → tín hiệu sai.
        for style_key, st_states in [("long", st_states_long), ("short", st_states_short)]:
            for ticker, state in st_states.items():
                bar = today_bars.get(ticker)
                if not bar:
                    continue

                price = bar["close"]
                alert_key = f"{ticker}_{style_key}"
                if alert_key in alerted_today:
                    continue

                buy_signal  = (state["trend"] == -1) and (price > state["dn"])
                sell_signal = (state["trend"] == 1)  and (price < state["up"])

                if buy_signal:
                    _st = state["dn"]
                    flips.append({
                        "ticker": ticker, "direction": "buy",
                        "price": price, "st": _st, "style": style_key,
                        "exchange": exchange_map.get(ticker, ""),
                    })
                    alerted_today.add(alert_key)
                    _save_signal_to_db(ticker, "MUA", style_key, price=price, st=_st)
                    alerted_signals[alert_key] = {
                        "direction": "buy", "price": price,
                        "time": now.strftime("%H:%M"), "style": style_key,
                    }
                elif sell_signal:
                    _st = state["up"]
                    flips.append({
                        "ticker": ticker, "direction": "sell",
                        "price": price, "st": _st, "style": style_key,
                        "exchange": exchange_map.get(ticker, ""),
                    })
                    alerted_today.add(alert_key)
                    _save_signal_to_db(ticker, "BÁN", style_key, price=price, st=_st)
                    alerted_signals[alert_key] = {
                        "direction": "sell", "price": price,
                        "time": now.strftime("%H:%M"), "style": style_key,
                    }

        # ── Load TK và thông tin vị thế từ scan_results (dùng cho P&L bán) ──
        if flips:
            flip_tickers = [f["ticker"] for f in flips]
            try:
                from scanner.database import load_avg_turnover, db_cursor
                tk_map = load_avg_turnover(flip_tickers)
                # Đọc giá mua gần nhất từ scan_results (trạng thái trước khi flip)
                with db_cursor(commit=False) as _cur:
                    _cur.execute(
                        """
                        SELECT ticker,
                               long_last_signal_date,  long_last_signal_price,
                               short_last_signal_date, short_last_signal_price
                        FROM scan_results WHERE ticker = ANY(%s)
                        """,
                        (flip_tickers,),
                    )
                    pos_map = {r["ticker"]: dict(r) for r in _cur.fetchall()}
            except Exception:
                tk_map, pos_map = {}, {}

            # Loại mã TK TB20 < 5 tỷ (mức "Rất thấp")
            flips = [f for f in flips if tk_map.get(f["ticker"], 0) / 1e6 >= 5.0]
            if len(flips) < len(flip_tickers):
                dropped = len(flip_tickers) - len(flips)
                logger.info(f"  Bo qua {dropped} flip vi TK TB20 < 5 ty")

        # ── Gửi Telegram cho từng flip ──
        for flip in flips:
            ticker = flip["ticker"]
            price  = flip["price"]
            st     = flip["st"]
            style  = flip["style"]
            tk     = float(tk_map.get(ticker, 0) or 0)
            tk_ty  = tk / 1e6
            tk_str = f"{tk_ty:.1f} tỷ ({tk_label(tk_ty)})" if tk > 0 else "–"
            time_str = now.strftime("%d/%m/%Y %H:%M")

            exch    = flip.get("exchange", "")
            ticker_link = tv_link(ticker, exch)
            if flip["direction"] == "buy":
                msg = (
                    f"🟢 <b>{ticker_link}</b> — Vùng mua tốt\n"
                    f"Giá vừa bứt phá ngưỡng kháng cự lên trên\n"
                    f"Giá hiện tại  : <b>{fmt_price(price)}</b>\n"
                    f"Kháng cự      : {fmt_price(st)}\n"
                    f"Thanh khoản   : {tk_str}\n"
                    f"⏰ {time_str}"
                )
            else:
                pnl_str = ""
                pos = pos_map.get(ticker, {})
                buy_price_key = f"{style}_last_signal_price"
                buy_date_key  = f"{style}_last_signal_date"
                buy_p  = float(pos.get(buy_price_key) or 0)
                buy_dt = fmt_date(pos.get(buy_date_key))
                if buy_p > 0:
                    pnl = round((price - buy_p) / buy_p * 100, 2)
                    try:
                        from datetime import date as _date
                        hold = (_date.today() - pd.Timestamp(buy_dt).date()).days if buy_dt else 0
                        hold_str = f"{hold} ngày"
                    except Exception:
                        hold_str = "–"
                    pnl_str = (
                        f"\nVị thế        : mua {buy_dt} | {hold_str}"
                        f"\nGiá mua       : {fmt_price(buy_p)} → {pnl:+.2f}%"
                    )
                msg = (
                    f"🔴 <b>{ticker_link}</b> — Vùng cân nhắc thoát lệnh\n"
                    f"Giá vừa thủng ngưỡng hỗ trợ\n"
                    f"Giá hiện tại  : <b>{fmt_price(price)}</b>\n"
                    f"Hỗ trợ        : {fmt_price(st)}\n"
                    f"Thanh khoản   : {tk_str}"
                    f"{pnl_str}\n"
                    f"⏰ {time_str}"
                )

            send_message(msg, style=flip["style"])

            # Gửi DM cá nhân cho user đang theo dõi mã này
            try:
                from scanner.database import get_holders_for_ticker
                holders = get_holders_for_ticker(ticker, flip["style"])
                for chat_id_holder in holders:
                    send_to_user(chat_id_holder, msg, style=flip["style"])
            except Exception as _e:
                logger.warning(f"  DM holdings failed ({ticker}): {_e}")

            logger.info(f"  FLIP {flip['style'].upper()} {flip['direction'].upper()}: {ticker} giá={price:,.2f} ST={st:,.2f}")

        total_flips += len(flips)
        logger.info(f"  Upsert: {len(today_bars)} | Flip: {len(flips)} | Tổng: {total_flips}")

        elapsed = (datetime.now(ICT) - now).total_seconds()
        sleep_sec = max(0, interval - elapsed)
        if sleep_sec > 0:
            time.sleep(sleep_sec)

    # ── Báo cáo rút chân: flip trong phiên nhưng đóng cửa ngược chiều ──
    _save_rut_chan_report(alerted_signals, st_states_long, st_states_short)

    long_flips  = sum(1 for k in alerted_today if k.endswith("_long"))
    short_flips = sum(1 for k in alerted_today if k.endswith("_short"))
    _close_lines = {
        "morning": {
            0: "Phiên sáng xong rồi. Đi ăn cơm đi, 13:00 chiều vào tiếp nhé.",
            1: "Nghỉ trưa thôi anh em, chiều gặp lại.",
            2: "Xong buổi sáng rồi. Tranh thủ nghỉ ngơi, chiều còn một phiên nữa.",
            3: "Hết phiên sáng rồi. Ăn gì ngon ngon đi, chiều về vào tiếp.",
            4: "Phiên sáng cuối tuần kết thúc. Nghỉ trưa nhanh, chiều chốt tuần luôn.",
        },
        "afternoon": {
            0: "Xong ngày đầu tuần. Về nhà nghỉ ngơi, mai chiến tiếp.",
            1: "Phiên chiều kết thúc. Về nhà thôi, báo cáo cuối ngày gửi sau.",
            2: "Qua được giữa tuần rồi. Về nhà đi, mai tiếp tục.",
            3: "Xong rồi, còn một ngày nữa là cuối tuần. Cố nốt ngày mai nhé.",
            4: "Tuần này xong rồi anh em ơi. Tắt máy, nghỉ cuối tuần — báo cáo cuối ngày gửi sau.",
        },
    }
    _end_text = _close_lines.get(session, {}).get(_wd, "Phiên giao dịch kết thúc. Hẹn gặp lại phiên sau.")
    send_message(_end_text, style="long")
    send_message(_end_text, style="short")
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
        "--session",
        choices=["morning", "afternoon", "full"],
        default="full",
        help="Phiên: morning=9:00-11:30 | afternoon=13:00-14:45 | full=cả ngày",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Chạy 1 cycle dù ngoài giờ giao dịch (để test)",
    )
    args = parser.parse_args()

    if args.mode == "pre":
        run_pre_session(force=args.force)
    elif args.mode == "session":
        if args.force:
            _run_session_once()
        else:
            run_session(interval=args.interval * 60, session=args.session)
    else:
        run_realtime(mode=args.mode, interval=args.interval * 60)
