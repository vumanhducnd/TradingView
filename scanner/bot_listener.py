"""
Telegram Bot Listener — nhận lệnh từ người dùng và trả lời tự động.

Cách dùng:
  python -m scanner.bot_listener

Lệnh hỗ trợ:
  /start       — Hướng dẫn sử dụng
  VIC          — Xem thông tin mã VIC
  /top         — Top 10 mã mạnh nhất hôm nay
  /mua         — Danh sách mã đang có tín hiệu MUA
  /ban         — Danh sách mã đang có tín hiệu BÁN
  /namgiu      — Danh sách mã đang nắm giữ (lệnh MUA chưa đóng)
"""

from __future__ import annotations

import time
import re
import requests
import urllib3
import scanner.utils  # noqa: patch SSL

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID
from scanner.utils import bias_label, fmt_price, logger

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
TICKER_RE = re.compile(r'^[A-Z]{2,4}$')
POLL_INTERVAL = 2  # giây


def _get(endpoint: str, params: dict = None) -> dict:
    resp = requests.get(f"{BASE_URL}/{endpoint}", params=params, timeout=15, verify=False)
    return resp.json()


def _send(chat_id: int | str, text: str) -> None:
    requests.post(f"{BASE_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=10, verify=False)


def _ticker_info(ticker: str) -> str:
    """Lấy thông tin mã từ scan_results DB."""
    try:
        from scanner.database import db_cursor, load_scan_dates
        dates = load_scan_dates()
        if not dates:
            return f"❌ Chưa có dữ liệu scan. Chạy scanner trước."
        last_date = dates[0]

        with db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT * FROM scan_results WHERE ticker = %s AND scan_date = %s",
                (ticker.upper(), last_date)
            )
            row = cur.fetchone()

        if not row:
            return f"❌ Không tìm thấy <b>{ticker}</b> trong dữ liệu ngày {last_date}."

        # Xác định lệnh hiện tại — ưu tiên long_, fallback sang field gốc
        # Ưu tiên short (ngắn hạn), fallback long (dài hạn)
        last_sig       = (row.get("short_last_signal_type")  or row.get("long_last_signal_type")  or row.get("last_signal_type")  or "")
        last_date_sig  = (row.get("short_last_signal_date")  or row.get("long_last_signal_date")  or row.get("last_signal_date")  or "")
        last_price     = (row.get("short_last_signal_price") or row.get("long_last_signal_price") or row.get("last_signal_price"))
        bars           = (row.get("short_bars_since_signal") or row.get("long_bars_since_signal") or row.get("bars_since_signal") or 0)
        pnl            = (row.get("short_signal_pnl_pct")    or row.get("long_signal_pnl_pct")    or row.get("signal_pnl_pct"))

        # Nếu DB thiếu → tính lại từ OHLCV (ngắn hạn trước)
        if not last_sig or not last_date_sig:
            try:
                from scanner.database import load_ohlcv
                from scanner.indicators import analyze_ticker
                df_ohlcv = load_ohlcv(ticker.upper(), days=400)
                if not df_ohlcv.empty:
                    info = analyze_ticker(df_ohlcv, style="short")
                    last_sig      = info.get("last_signal_type", "")
                    last_date_sig = info.get("last_signal_date", "")
                    last_price    = info.get("last_signal_price")
                    bars          = info.get("bars_since_signal", 0)
                    pnl           = info.get("signal_pnl_pct")
            except Exception:
                pass
        bias = float(row.get("bias_norm") or 0)
        close = float(row.get("close") or 0)
        tk = float(row.get("turnover") or 0)

        lenh = "🟢 Nắm giữ" if last_sig == "MUA" else ("🔴 Đứng ngoài" if last_sig == "BÁN" else "⬜ Chưa có")
        pnl_str = f"{pnl:+.2f}%" if pnl is not None else "N/A"
        tk_str = f"{tk/1e9:.1f} tỷ" if tk else "N/A"

        # Tín hiệu mới hôm nay
        long_buy  = bool(row.get("long_buy_signal"))
        long_sell = bool(row.get("long_sell_signal"))
        short_buy = bool(row.get("short_buy_signal"))
        short_sell= bool(row.get("short_sell_signal"))
        tin_hieu = []
        if short_buy: tin_hieu.append("🟢 MUA Ngắn hạn")
        if long_buy:  tin_hieu.append("🟢 MUA Dài hạn")
        if short_sell:tin_hieu.append("🔴 BÁN Ngắn hạn")
        if long_sell: tin_hieu.append("🔴 BÁN Dài hạn")
        tin_hieu_str = " | ".join(tin_hieu) if tin_hieu else "Không có tín hiệu mới"

        lines = [
            f"📊 <b>{ticker.upper()}</b> — {last_date}",
            f"",
            f"Lệnh HĐ  : {lenh}",
            f"Vào lệnh : {str(last_date_sig)[:10]} @ {fmt_price(last_price) if last_price else 'N/A'}",
            f"Giữ lệnh : {bars} phiên",
            f"Lời/Lỗ  : <b>{pnl_str}</b>",
            f"",
            f"Giá HT   : {fmt_price(close)}",
            f"BiasNorm : {bias:.0f}/100 ({bias_label(bias)})",
            f"TK/ngày  : {tk_str}",
            f"",
            f"Tín hiệu : {tin_hieu_str}",
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Lỗi: {e}"


def _top_stocks(n: int = 10) -> str:
    """Top N mã mạnh nhất theo BiasNorm."""
    try:
        from scanner.database import db_cursor, load_scan_dates
        dates = load_scan_dates()
        if not dates:
            return "Chưa có dữ liệu scan."
        with db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT ticker, bias_norm, long_last_signal_type FROM scan_results "
                "WHERE scan_date = %s ORDER BY bias_norm DESC LIMIT %s",
                (dates[0], n)
            )
            rows = cur.fetchall()
        lines = [f"🏆 <b>Top {n} mạnh nhất — {dates[0]}</b>\n"]
        for r in rows:
            lenh = "🟢" if r["long_last_signal_type"] == "MUA" else "🔴"
            lines.append(f"{lenh} {r['ticker']}: {float(r['bias_norm'] or 0):.0f}/100")
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Lỗi: {e}"


def _signal_list(signal_col: str, label: str) -> str:
    """Danh sách mã có tín hiệu hôm nay."""
    try:
        from scanner.database import db_cursor, load_scan_dates
        dates = load_scan_dates()
        if not dates:
            return "Chưa có dữ liệu scan."
        with db_cursor(commit=False) as cur:
            cur.execute(
                f"SELECT ticker, bias_norm, turnover FROM scan_results "
                f"WHERE scan_date = %s AND ({signal_col} = TRUE) "
                f"ORDER BY bias_norm DESC",
                (dates[0],)
            )
            rows = cur.fetchall()
        if not rows:
            return f"Không có mã {label} hôm nay."
        lines = [f"{'🟢' if 'MUA' in label else '🔴'} <b>{label} — {dates[0]}</b>\n"]
        for r in rows:
            tk = float(r.get("turnover") or 0)
            lines.append(
                f"• {r['ticker']} — Bias {float(r['bias_norm'] or 0):.0f} | TK {tk/1e9:.1f}tỷ"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Lỗi: {e}"


def _holding_list() -> str:
    """Danh sách mã đang nắm giữ."""
    try:
        from scanner.database import db_cursor, load_scan_dates
        dates = load_scan_dates()
        if not dates:
            return "Chưa có dữ liệu scan."
        with db_cursor(commit=False) as cur:
            cur.execute(
                "SELECT ticker, signal_pnl_pct, bars_since_signal, bias_norm "
                "FROM scan_results "
                "WHERE scan_date = %s AND last_signal_type = 'MUA' "
                "ORDER BY signal_pnl_pct DESC NULLS LAST",
                (dates[0],)
            )
            rows = cur.fetchall()
        if not rows:
            return "Không có mã nào đang nắm giữ."
        lines = [f"💼 <b>Đang nắm giữ — {dates[0]}</b> ({len(rows)} mã)\n"]
        for r in rows:
            pnl = r.get("signal_pnl_pct")
            pnl_str = f"{float(pnl):+.1f}%" if pnl else "N/A"
            ico = "🟢" if pnl and float(pnl) >= 0 else "🔴"
            lines.append(
                f"{ico} {r['ticker']}: {pnl_str} | {r.get('bars_since_signal', 0)} phiên"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Lỗi: {e}"


HELP_TEXT = """
🤖 <b>ManhDucCapital Bot</b>

Gõ <b>mã cổ phiếu</b> (VD: VIC, FPT) để xem thông tin.

Lệnh nhanh:
  /top     — Top 10 mạnh nhất hôm nay
  /mua     — Tín hiệu MUA hôm nay
  /ban     — Tín hiệu BÁN hôm nay
  /namgiu  — Danh sách đang nắm giữ
""".strip()


def run() -> None:
    logger.info("Bot Listener đang chạy... Gõ Ctrl+C để dừng.")
    offset = 0

    while True:
        try:
            data = _get("getUpdates", {"offset": offset, "timeout": 10})
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                text = msg.get("text", "").strip()
                if not text:
                    continue

                text_upper = text.upper().strip()
                logger.info(f"Nhận: '{text}' từ chat {chat_id}")

                if text in ("/start", "/help"):
                    _send(chat_id, HELP_TEXT)
                elif text == "/top":
                    _send(chat_id, _top_stocks(10))
                elif text in ("/mua", "/MUA"):
                    _send(chat_id, _signal_list(
                        "long_buy_signal OR short_buy_signal", "TÍN HIỆU MUA"
                    ))
                elif text in ("/ban", "/BAN", "/bán"):
                    _send(chat_id, _signal_list(
                        "long_sell_signal OR short_sell_signal", "TÍN HIỆU BÁN"
                    ))
                elif text in ("/namgiu", "/nắm giữ"):
                    _send(chat_id, _holding_list())
                elif TICKER_RE.match(text_upper):
                    _send(chat_id, _ticker_info(text_upper))
                else:
                    _send(chat_id, f"❓ Không hiểu lệnh '<b>{text}</b>'\n\nGõ /start để xem hướng dẫn.")

        except KeyboardInterrupt:
            logger.info("Bot dừng.")
            break
        except Exception as e:
            logger.warning(f"Bot error: {e}")
            time.sleep(5)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
