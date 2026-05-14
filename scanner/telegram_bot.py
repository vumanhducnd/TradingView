"""
Telegram notification module.
Uses requests (synchronous) — no asyncio needed for GitHub Actions.
"""

import math
import time
import warnings
import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import date

from scanner.config import (
    TELEGRAM_API, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN_SHORT, TELEGRAM_CHAT_ID_SHORT,
)
from scanner.utils import bias_label, fmt_price, logger


def _val(row, *names, default=None):
    """Lấy giá trị đầu tiên không-NaN từ danh sách tên cột (hỗ trợ single + dual mode)."""
    for name in names:
        v = row.get(name)
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return default


def _fmt(v, default="–") -> str:
    """fmt_price với fallback khi NaN/None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return fmt_price(v)


def _fmt_tk(row) -> str:
    """Thanh khoản: ưu tiên turnover (tỷ VND), fallback volume (triệu cp)."""
    tk = _val(row, "turnover")
    if tk and tk > 0:
        return f"{tk / 1e9:.1f} tỷ"
    vol = _val(row, "volume")
    if vol and vol > 0:
        return f"{vol / 1e6:.1f}M cp"
    return "–"

_CRITERIA_LABELS = {
    "ema":    "EMA9>21",
    "vwap":   "Giá>VWAP",
    "rsi":    "RSI>52",
    "macd":   "MACD↑",
    "adx":    "ADX>20",
    "obv":    "OBV↑",
    "stoch":  "Stoch↑",
    "candle": "Nến trên",
    "vol":    "Vol↑",
}


def _credentials(style: str) -> tuple[str, str]:
    """Trả về (token, chat_id) theo style: 'long' | 'short'."""
    if style == "short":
        return TELEGRAM_TOKEN_SHORT, TELEGRAM_CHAT_ID_SHORT
    return TELEGRAM_TOKEN, TELEGRAM_CHAT_ID


def _send_raw(text: str, token: str, chat_id: str, parse_mode: str = "HTML") -> bool:
    if not token or not chat_id:
        logger.warning("Telegram credentials not set — skipping")
        return False
    url = TELEGRAM_API.format(token=token)
    try:
        resp = requests.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
            verify=False,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def send_message(text: str, style: str = "long", parse_mode: str = "HTML") -> bool:
    """Gửi tin nhắn đến bot theo style ('long' | 'short'). Returns True on success."""
    token, chat_id = _credentials(style)
    return _send_raw(text, token, chat_id, parse_mode)


def send_message_both(text: str, parse_mode: str = "HTML") -> None:
    """Gửi cùng 1 tin đến cả 2 bot (dùng cho header/summary chung)."""
    _send_raw(text, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, parse_mode)
    # Chỉ gửi lần 2 nếu short bot khác long bot
    if TELEGRAM_TOKEN_SHORT != TELEGRAM_TOKEN or TELEGRAM_CHAT_ID_SHORT != TELEGRAM_CHAT_ID:
        time.sleep(0.2)
        _send_raw(text, TELEGRAM_TOKEN_SHORT, TELEGRAM_CHAT_ID_SHORT, parse_mode)


def send_file(file_path: str, caption: str = "", style: str = "long") -> bool:
    """Gửi file qua Telegram Bot."""
    token, chat_id = _credentials(style)
    if not token or not chat_id:
        logger.warning("Telegram credentials not set — skipping file send")
        return False
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
                files={"document": (file_path.split("\\")[-1].split("/")[-1], f)},
                timeout=60,
                verify=False,
            )
        resp.raise_for_status()
        logger.info(f"File sent: {file_path}")
        return True
    except Exception as e:
        logger.warning(f"Telegram send_file failed: {e}")
        return False


def send_daily_report(
    results: pd.DataFrame,
    signals: dict[str, pd.DataFrame],
    ai_analysis: dict | None = None,
    super_stocks: pd.DataFrame | None = None,
    intraday_reversals: dict[str, list[str]] | None = None,
) -> None:
    """
    Gửi báo cáo cuối ngày.
    Dual mode: mỗi bot nhận header + positions + tín hiệu riêng của style đó.
    Single mode: tất cả → bot dài hạn.
    """
    today = date.today().strftime("%d/%m/%Y")
    is_dual = "long_buy_signal" in results.columns

    TOP_N = 5
    # Ưu tiên turnover (giá trị giao dịch), fallback volume
    vol_col = "turnover" if "turnover" in results.columns else ("volume" if "volume" in results.columns else None)

    def _top_n(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        return df.nlargest(TOP_N, vol_col) if vol_col and vol_col in df.columns else df.head(TOP_N)

    def _signal_lines(df: pd.DataFrame, emoji: str, label: str, st_col: str,
                      direction: str = "buy") -> list[str]:
        top = _top_n(df)
        if top.empty:
            return []
        total = len(df)
        shown = len(top)
        count_str = f"{shown}/{total}" if shown < total else f"{shown}"
        lines = [f"{emoji} <b>{label} — {count_str} mã</b>"]
        for _, row in top.iterrows():
            st    = _val(row, st_col, "supertrend")
            close = _val(row, "close")
            if direction == "buy":
                # SL = ST - 2%
                sl_str = _fmt(float(st) * 0.98) if st and float(st) > 0 else "–"
                lines.append(
                    f"  <b>{row['ticker']}</b>\n"
                    f"    Giá mua       : {_fmt(close)}\n"
                    f"    Giá SL (ST-2%): {sl_str}\n"
                    f"    Thanh khoản   : {_fmt_tk(row)}"
                )
            else:
                lines.append(
                    f"  <b>{row['ticker']}</b>\n"
                    f"    Giá bán    : {_fmt(close)}\n"
                    f"    Thanh khoản: {_fmt_tk(row)}"
                )
        return lines

    def _send_for_style(style: str) -> None:
        """Gửi toàn bộ report (header + positions + tín hiệu) cho 1 style."""
        p = f"{style}_"
        is_long = style == "long"
        label   = "📈 Dài hạn (10/3.0)" if is_long else "⚡ Ngắn hạn (7/2.0)"

        buy_df  = results[results[f"{p}buy_signal"].astype(bool)]  if f"{p}buy_signal"  in results.columns else pd.DataFrame()
        sell_df = results[results[f"{p}sell_signal"].astype(bool)] if f"{p}sell_signal" in results.columns else pd.DataFrame()
        no_sig  = len(results) - len(buy_df) - len(sell_df)

        style_name = "Đầu tư Dài Hạn" if is_long else "Đầu tư Ngắn Hạn"

        # Top tăng/giảm mạnh trong ngày theo TK
        top_gainers, top_losers = [], []
        try:
            from scanner.database import load_daily_change
            tickers_all = results["ticker"].tolist()
            chg_map = load_daily_change(tickers_all)
            tk_series = results.set_index("ticker")["turnover"] if "turnover" in results.columns else None
            def _sort_by_tk(t_list):
                if tk_series is None:
                    return t_list
                return sorted(t_list, key=lambda t: float(tk_series.get(t, 0) or 0), reverse=True)
            gainers = _sort_by_tk([t for t, v in chg_map.items() if v > 0])[:5]
            losers  = _sort_by_tk([t for t, v in chg_map.items() if v < 0])[:5]
            top_gainers = [f"{t}({chg_map[t]:+.1f}%)" for t in gainers]
            top_losers  = [f"{t}({chg_map[t]:+.1f}%)" for t in losers]
        except Exception as e:
            logger.warning(f"load_daily_change failed: {e}")

        # AI cuối phiên (chạy trước để gộp vào header)
        ai_end = ""
        try:
            from scanner.ai_analyst import generate_end_of_session_ai
            ai_end = generate_end_of_session_ai(
                results=results,
                buy_tickers=buy_df["ticker"].tolist() if not buy_df.empty else [],
                sell_tickers=sell_df["ticker"].tolist() if not sell_df.empty else [],
                style_label=style_name,
                top_gainers=top_gainers,
                top_losers=top_losers,
                intraday_reversals=intraday_reversals,
            )
        except Exception as e:
            logger.warning(f"AI cuoi phien failed: {e}")

        # Header + AI gộp 1 tin
        header_lines = [
            f"<b>📊 Báo cáo cuối phiên — {style_name} — {today}</b>",
            f"🟢 Bứt phá: <b>{len(buy_df)}</b> mã  |  🔴 Đảo chiều: <b>{len(sell_df)}</b> mã",
        ]
        if ai_end:
            header_lines.append(f"\n{ai_end}")
        send_message("\n".join(header_lines), style=style)
        time.sleep(0.8)

        # Siêu cổ phiếu đã bỏ khỏi Telegram (vẫn còn trong Excel)

        # Top 5 vùng xanh (nắm giữ) theo TK thay vị thế
        _send_top_vung_xanh(results, style=style)
        time.sleep(0.8)

        # Tín hiệu
        st_col = f"{p}supertrend"
        for lines in [
            _signal_lines(buy_df,  "🚀", "Tín hiệu bứt phá xác nhận", st_col, direction="buy"),
            _signal_lines(sell_df, "🔻", "Tín hiệu đảo chiều giảm",   st_col, direction="sell"),
        ]:
            if lines:
                send_message("\n".join(lines), style=style)
                time.sleep(0.5)

        if buy_df.empty and sell_df.empty:
            top5 = results.nlargest(5, "bias_norm")[["ticker", "bias_norm"]]
            txt = "\n".join(f"  • {r['ticker']}: {r['bias_norm']:.0f}/100" for _, r in top5.iterrows())
            send_message(f"Hôm nay không có tín hiệu mới.\n\n<b>Top 5 mạnh nhất:</b>\n{txt}", style=style)

    if is_dual:
        _send_for_style("long")
        time.sleep(1)
        _send_for_style("short")
    else:
        buy_df  = signals.get("buy",  pd.DataFrame())
        sell_df = signals.get("sell", pd.DataFrame())
        header = (
            f"<b>📊 ManhDucCapital Scanner — {today}</b>\n"
            f"📈 Dài hạn (ATR=10, x3.0)\n\n"
            f"🟢 MUA: <b>{len(buy_df)}</b> mã\n"
            f"🔴 BÁN: <b>{len(sell_df)}</b> mã\n"
            f"⬜ Không tín hiệu: {len(results) - len(buy_df) - len(sell_df)} mã"
        )
        send_message(header, style="long")
        time.sleep(0.8)
        if ai_analysis and ai_analysis.get("overview"):
            send_message(f"<b>🤖 Nhận định AI:</b>\n{ai_analysis['overview']}", style="long")
            time.sleep(0.8)
        _send_positions_summary(results, style="long")
        time.sleep(0.8)
        for lines in [
            _signal_lines(buy_df,  "🚀", "Tín hiệu bứt phá xác nhận", "supertrend", direction="buy"),
            _signal_lines(sell_df, "🔻", "Tín hiệu đảo chiều giảm",   "supertrend", direction="sell"),
        ]:
            if lines:
                send_message("\n".join(lines), style="long")
                time.sleep(0.5)
        if buy_df.empty and sell_df.empty:
            top5 = results.nlargest(5, "bias_norm")[["ticker", "bias_norm"]]
            txt = "\n".join(f"  • {r['ticker']}: {r['bias_norm']:.0f}/100" for _, r in top5.iterrows())
            send_message(f"Hôm nay không có tín hiệu mới.\n\n<b>Top 5 mạnh nhất:</b>\n{txt}", style="long")

    # ── Excel — mỗi file gửi đúng bot ────────────────────────────────────────
    try:
        from scanner.excel_report import build_excel_report
        excel_paths = build_excel_report(results, signals, ai_analysis=ai_analysis, super_stocks=super_stocks)
        for file_style, path in excel_paths.items():
            if path:
                send_file(
                    path,
                    caption=f"📊 Report {'Dài hạn' if file_style == 'long' else 'Ngắn hạn'} {today}",
                    style=file_style,
                )
                time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Excel report failed: {e}")


def _format_signal(row: pd.Series, signal_type: str) -> str:
    emoji = "🟢" if signal_type == "MUA" else "🔴"
    bn = row.get("bias_norm", 0)
    label = bias_label(bn)
    close_fmt = fmt_price(row.get("close", 0))
    st_fmt = fmt_price(row.get("supertrend", 0))

    # Bull/bear criteria breakdown
    criteria = _CRITERIA_LABELS
    bull_parts = []
    for key, display in criteria.items():
        val = row.get(f"bull_{key}", False)
        bull_parts.append(f"{'✅' if val else '❌'}{display}")
    criteria_line = "  " + "  ".join(bull_parts[:5]) + "\n  " + "  ".join(bull_parts[5:])

    b_score = int(row.get("b_score", 0))

    # Hỗ trợ / Kháng cự
    support    = row.get("support", 0)
    resistance = row.get("resistance", 0)
    dist_ht    = row.get("dist_support_pct")
    dist_kc    = row.get("dist_resistance_pct")
    ht_str = f"{fmt_price(support)} ({dist_ht:+.1f}%)" if dist_ht is not None else fmt_price(support)
    kc_str = f"{fmt_price(resistance)} ({dist_kc:+.1f}%)" if dist_kc is not None else fmt_price(resistance)

    # Vị thế đang mở
    buy_date  = str(row.get("buy_date", ""))[:10]
    buy_price = row.get("buy_price")
    pnl       = row.get("pnl_pct")
    position_line = ""
    if buy_price and pnl is not None:
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        position_line = f"\nVị thế: mua {buy_date} @ {fmt_price(buy_price)} | {pnl_emoji} {pnl:+.2f}%"

    lines = [
        f"{emoji} <b>{signal_type} - {row['ticker']}</b>",
        f"BiasNorm: <b>{bn:.0f}/100</b> ({label})",
        f"Giá đóng cửa: <b>{close_fmt}</b>",
        f"SuperTrend: {st_fmt}",
        f"Hỗ trợ  (HT): {ht_str}",
        f"Kháng cự (KC): {kc_str}",
        position_line,
        f"",
        f"Bull criteria ({b_score}/9):",
        criteria_line,
        f"",
        f"<i>Tín hiệu: hôm nay (đảo chiều {('lên' if signal_type == 'MUA' else 'xuống')})</i>",
    ]
    return "\n".join(l for l in lines if l != "" or l == "")


def _send_super_stocks(df: pd.DataFrame, style: str = "long") -> None:
    """Gửi danh sách siêu cổ phiếu vùng mua."""
    lines = ["<b>⭐ SIÊU CỔ PHIẾU VÙNG MUA:</b>"]

    for _, row in df.iterrows():
        ticker   = row["ticker"]
        score    = row.get("super_score", 0)
        bias     = row.get("bias_norm", 0)
        close    = row.get("close", 0)
        sup      = row.get("long_support") or row.get("long_supertrend") or 0
        res      = row.get("long_resistance") or 0
        dist_s   = row.get("dist_support_pct", 0)
        dist_r   = row.get("dist_resistance_pct", 0)
        b_score  = row.get("b_score", 0)
        both     = row.get("both_buy", False)
        lbuy     = row.get("long_buy_signal", False)
        sbuy     = row.get("short_buy_signal", False)

        signal_tag = ""
        if both:
            signal_tag = " 🔥"
        elif lbuy and sbuy:
            signal_tag = " 🟢🟢"
        elif lbuy:
            signal_tag = " 🟢DH"
        elif sbuy:
            signal_tag = " 🟢NH"

        lines.append(
            f"\n<b>{ticker}</b>{signal_tag} | Score: {score:.0f}"
            f"\n  Giá: {fmt_price(close)} | Bias: {bias:.0f}/100 | Bull: {b_score}/9"
            f"\n  HT: {fmt_price(sup)} ({dist_s:+.1f}%) → KC: {fmt_price(res)} ({dist_r:+.1f}%)"
        )

    send_message("\n".join(lines), style=style)


def _send_top_vung_xanh(results: pd.DataFrame, style: str = "long", top_n: int = 5) -> None:
    """Top N mã vùng xanh (long_trend=1) theo TK cao nhất."""
    p = f"{style}_"
    trend_col = f"{p}trend" if f"{p}trend" in results.columns else "trend"
    df = results[results.get(trend_col, results.get("trend", pd.Series(dtype=int))) == 1].copy()
    if df.empty:
        return
    if "turnover" in df.columns:
        df = df.sort_values("turnover", ascending=False)
    df = df.head(top_n)

    date_col  = f"{p}last_signal_date"  if f"{p}last_signal_date"  in results.columns else "last_signal_date"
    price_col = f"{p}last_signal_price" if f"{p}last_signal_price" in results.columns else "last_signal_price"

    lines = [f"<b>💼 Top {top_n} vùng xanh (TK cao nhất):</b>"]
    for _, row in df.iterrows():
        ticker = row.get("ticker", "")
        close  = _val(row, "close")
        bd     = str(row.get(date_col) or "")[:10]
        buy_p  = _val(row, price_col)
        tk     = float(row.get("turnover") or 0)
        tk_str = f"{tk/1e9:.1f} tỷ" if tk > 0 else "–"

        pnl = round((float(close) - float(buy_p)) / float(buy_p) * 100, 2) \
              if close and buy_p and float(buy_p) > 0 else None
        pnl_str = f" | {pnl:+.1f}%" if pnl is not None else ""

        lines.append(f"  <b>{ticker}</b> | Giá {_fmt(close)} | TK {tk_str}{pnl_str} | Từ {bd}")

    send_message("\n".join(lines), style=style)


