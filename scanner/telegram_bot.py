"""
Telegram notification module.
Uses requests (synchronous) — no asyncio needed for GitHub Actions.
"""

import time
import warnings
import requests
import urllib3
import pandas as pd

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
from datetime import date

from scanner.config import TELEGRAM_API, TELEGRAM_CHAT_ID, TELEGRAM_TOKEN
from scanner.utils import bias_label, fmt_price, logger

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


def send_file(file_path: str, caption: str = "") -> bool:
    """Gửi file (Excel, PDF...) qua Telegram Bot."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set — skipping file send")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument"
    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
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


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Send a single Telegram message. Returns True on success."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not set — skipping notification")
        return False
    url = TELEGRAM_API.format(token=TELEGRAM_TOKEN)
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode}
    try:
        # verify=False: workaround for Windows SSL cert issue (safe for Telegram API)
        resp = requests.post(url, json=payload, timeout=10, verify=False)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def send_daily_report(
    results: pd.DataFrame,
    signals: dict[str, pd.DataFrame],
    ai_analysis: dict | None = None,
    super_stocks: pd.DataFrame | None = None,
) -> None:
    """Send full daily report: header + AI overview + super stocks + buy/sell signals."""
    buy_df = signals.get("buy", pd.DataFrame())
    sell_df = signals.get("sell", pd.DataFrame())
    today = date.today().strftime("%d/%m/%Y")

    # Header message
    is_dual = "long_buy_signal" in results.columns
    style_str = "Ngắn hạn (7/2.0) + Dài hạn (10/3.0)" if is_dual else "Dài hạn (ATR=10, x3.0)"
    both_buy_n  = len(signals.get("both_buy",  pd.DataFrame()))
    both_sell_n = len(signals.get("both_sell", pd.DataFrame()))
    both_line = f"  🔥 Đồng thuận cả 2 : <b>{both_buy_n + both_sell_n}</b> mã\n" if is_dual else ""

    header = (
        f"<b>📊 ManhDucCapital Scanner</b>\n"
        f"Quét: {today} (sau 15:30 ICT)\n"
        f"{style_str}\n\n"
        f"<b>Kết quả:</b>\n"
        f"  🟢 Tín hiệu MUA : <b>{len(buy_df)}</b> mã\n"
        f"  🔴 Tín hiệu BÁN : <b>{len(sell_df)}</b> mã\n"
        f"{both_line}"
        f"  ⬜ Không tín hiệu: <b>{len(results) - len(buy_df) - len(sell_df)}</b> mã"
    )
    send_message(header)
    time.sleep(1)

    # AI tổng quan thị trường
    if ai_analysis and ai_analysis.get("overview"):
        send_message(f"<b>🤖 Nhận định AI:</b>\n{ai_analysis['overview']}")
        time.sleep(1)

    # Siêu cổ phiếu vùng mua
    if super_stocks is not None and not super_stocks.empty:
        _send_super_stocks(super_stocks)
        time.sleep(1)

    # Tóm tắt vị thế đang mở
    _send_positions_summary(results)
    time.sleep(1)

    # Top 10 mua/bán có thanh khoản cao nhất
    TOP_N = 10
    vol_col = "volume" if "volume" in results.columns else None

    def _top10(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        if vol_col and vol_col in df.columns:
            return df.nlargest(TOP_N, vol_col)
        return df.head(TOP_N)

    buy_top  = _top10(buy_df)
    sell_top = _top10(sell_df)

    ai_buy  = {x["ticker"]: x.get("telegram_text", x.get("analysis", ""))
               for x in (ai_analysis or {}).get("buy_signals",  [])}
    ai_sell = {x["ticker"]: x.get("telegram_text", x.get("analysis", ""))
               for x in (ai_analysis or {}).get("sell_signals", [])}

    for _, row in buy_top.iterrows():
        send_message(_format_signal(row, signal_type="MUA"))
        time.sleep(0.5)
        ai_text = ai_buy.get(row["ticker"], "")
        if ai_text:
            send_message(f"🤖 <b>AI - {row['ticker']}:</b>\n{ai_text}")
            time.sleep(1)

    for _, row in sell_top.iterrows():
        send_message(_format_signal(row, signal_type="BÁN"))
        time.sleep(0.5)
        ai_text = ai_sell.get(row["ticker"], "")
        if ai_text:
            send_message(f"🤖 <b>AI - {row['ticker']}:</b>\n{ai_text}")
            time.sleep(1)

    # Không có tín hiệu → top 5 bias_norm
    if buy_df.empty and sell_df.empty:
        top5 = results.nlargest(5, "bias_norm")[["ticker", "bias_norm"]]
        lines = "\n".join(
            f"  • {r['ticker']}: {r['bias_norm']:.0f}/100"
            for _, r in top5.iterrows()
        )
        send_message(
            f"Hôm nay không có tín hiệu MUA/BÁN mới.\n\n"
            f"<b>Top 5 mạnh nhất:</b>\n{lines}"
        )

    # Gửi file Excel report
    try:
        from scanner.excel_report import build_excel_report
        excel_path = build_excel_report(results, signals, ai_analysis=ai_analysis, super_stocks=super_stocks)
        if excel_path:
            send_file(
                str(excel_path),
                caption=f"📊 Report {today} — {len(buy_df)} MUA | {len(sell_df)} BÁN",
            )
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


def _send_super_stocks(df: pd.DataFrame) -> None:
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

    send_message("\n".join(lines))


def _send_positions_summary(results: pd.DataFrame) -> None:
    """Gửi tóm tắt tất cả vị thế đang mở (có buy_date)."""
    if "buy_date" not in results.columns:
        return

    pos = results[results["buy_date"].notna()].copy()
    if pos.empty:
        return

    lines = ["<b>📋 Vị thế đang nắm giữ:</b>"]
    total_pnl = []

    if "pnl_pct" not in pos.columns:
        return
    for _, row in pos.sort_values("pnl_pct", ascending=False, na_position="last").iterrows():
        pnl   = row.get("pnl_pct")
        close = row.get("close", 0)
        sup   = row.get("support", 0)
        buy_p = row.get("buy_price", 0)

        pnl_str = f"{pnl:+.1f}%" if pnl is not None else "N/A"
        pnl_ico = "🟢" if (pnl or 0) >= 0 else "🔴"

        # Tránh lỗ tối đa từ giá mua đến stop loss
        max_loss = round((buy_p - sup) / buy_p * 100, 1) if buy_p and sup else None
        stop_str = f" | Stop: -{max_loss}%" if max_loss else ""

        lines.append(
            f"{pnl_ico} <b>{row['ticker']}</b>: {pnl_str}{stop_str}"
            f" | HT {fmt_price(sup)}"
        )
        if pnl is not None:
            total_pnl.append(pnl)

    if total_pnl:
        avg = sum(total_pnl) / len(total_pnl)
        lines.append(f"\n<b>Tổng {len(pos)} vị thế | Lời/Lỗ TB: {avg:+.1f}%</b>")

    send_message("\n".join(lines))
