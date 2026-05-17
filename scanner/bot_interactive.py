"""
Interactive Telegram bot — long-polling mode.
Chạy liên tục (Render/Railway worker), nhận lệnh từ người dùng.

Commands:
  /start | /help     — menu hướng dẫn
  /check VHM         — tra giá, BiasNorm, tín hiệu, gợi ý SL/TP
  /top [n]           — top N mã vùng xanh mạnh nhất (mặc định 10)
  /dangiu            — danh mục đang giữ + P&L
  /alert VHM 25.5    — đặt cảnh báo khi giá chạm 25.5
  /alerts            — xem cảnh báo đang theo dõi
  /delalert <id>     — xoá cảnh báo
"""

import math
import time

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.config import TELEGRAM_TOKEN
from scanner.utils import bias_label, fmt_price, logger


# ─── Telegram helpers ─────────────────────────────────────────────────────────

def _api(token: str, method: str) -> str:
    return f"https://api.telegram.org/bot{token}/{method}"


def _get_updates(token: str, offset: int, timeout: int = 30) -> list[dict]:
    try:
        resp = requests.get(
            _api(token, "getUpdates"),
            params={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=timeout + 5,
            verify=False,
        )
        return resp.json().get("result", [])
    except Exception as e:
        logger.warning(f"getUpdates error: {e}")
        return []


def _reply(token: str, chat_id: int | str, text: str, keyboard: dict | None = None) -> None:
    body: dict = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if keyboard:
        body["reply_markup"] = keyboard
    try:
        requests.post(_api(token, "sendMessage"), json=body, timeout=15, verify=False)
    except Exception as e:
        logger.warning(f"send_reply error (chat={chat_id}): {e}")


# ─── Keyboard layouts ─────────────────────────────────────────────────────────

_KB_MAIN = {
    "inline_keyboard": [
        [{"text": "📖 Hướng dẫn giao dịch", "callback_data": "guide"}],
        [
            {"text": "🔍 Tra cứu mã",    "callback_data": "input_check"},
            {"text": "🏆 Top vùng xanh", "callback_data": "top_menu"},
        ],
        [
            {"text": "💼 Đang giữ",      "callback_data": "dangiu"},
            {"text": "⏰ Đặt cảnh báo", "callback_data": "input_alert"},
        ],
        [{"text": "📋 Cảnh báo của tôi", "callback_data": "alerts"}],
    ]
}

_KB_BACK = {
    "inline_keyboard": [[{"text": "🔙 Menu chính", "callback_data": "main_menu"}]]
}

_KB_TOP = {
    "inline_keyboard": [
        [
            {"text": "Top 5",  "callback_data": "top_5"},
            {"text": "Top 10", "callback_data": "top_10"},
            {"text": "Top 20", "callback_data": "top_20"},
        ],
        [{"text": "🔙 Menu chính", "callback_data": "main_menu"}],
    ]
}


# ─── Conversation state (chờ user nhập mã / giá) ─────────────────────────────

_user_state: dict[str, str] = {}  # chat_id (str) → "check" | "alert"


# ─── Telegram API helpers ─────────────────────────────────────────────────────

def _answer_callback(token: str, callback_id: str) -> None:
    try:
        requests.post(
            _api(token, "answerCallbackQuery"),
            json={"callback_query_id": callback_id},
            timeout=10,
            verify=False,
        )
    except Exception:
        pass


def _send_main_menu(token: str, chat_id: int | str) -> None:
    _reply(
        token, chat_id,
        "👋 Chào mừng đến với <b>MDAlpha3 Bot</b>!\nChọn tính năng bạn muốn dùng:",
        _KB_MAIN,
    )


def _handle_callback(token: str, cq: dict) -> None:
    _answer_callback(token, cq["id"])
    chat_id = cq["message"]["chat"]["id"]
    cid_str = str(chat_id)
    data    = cq.get("data", "")

    if data == "main_menu":
        _user_state.pop(cid_str, None)
        _send_main_menu(token, chat_id)

    elif data == "guide":
        _reply(token, chat_id, GUIDE, _KB_BACK)

    elif data == "input_check":
        _user_state[cid_str] = "check"
        _reply(token, chat_id, "🔍 Nhập mã cổ phiếu (VD: <code>VHM</code>):")

    elif data == "top_menu":
        _reply(token, chat_id, "Chọn số lượng mã muốn xem:", _KB_TOP)

    elif data in ("top_5", "top_10", "top_20"):
        n = int(data.split("_")[1])
        _reply(token, chat_id, _cmd_top(n), _KB_BACK)

    elif data == "dangiu":
        _reply(token, chat_id, _cmd_dangiu(), _KB_BACK)

    elif data == "input_alert":
        _user_state[cid_str] = "alert"
        _reply(token, chat_id, "⏰ Nhập mã và giá mục tiêu (VD: <code>VHM 25.5</code>):")

    elif data == "alerts":
        text, kb = _cmd_list_alerts(cid_str)
        _reply(token, chat_id, text, kb)

    elif data.startswith("del_"):
        aid = data[4:]
        reply = _cmd_delete_alert(cid_str, aid)
        # Sau khi xoá, hiện lại danh sách cập nhật
        text, kb = _cmd_list_alerts(cid_str)
        _reply(token, chat_id, reply)
        _reply(token, chat_id, text, kb)


# ─── Scan results cache (TTL 5 phút) ─────────────────────────────────────────

_cache_df: pd.DataFrame | None = None
_cache_ts: float = 0.0
_CACHE_TTL = 300


def _scan_df() -> pd.DataFrame:
    global _cache_df, _cache_ts
    if _cache_df is None or time.time() - _cache_ts > _CACHE_TTL:
        from scanner.database import load_scan_results
        _cache_df = load_scan_results()
        _cache_ts = time.time()
    return _cache_df


# ─── Format helpers ───────────────────────────────────────────────────────────

def _val(row, *names, default=None):
    for name in names:
        v = row.get(name) if isinstance(row, dict) else (
            row[name] if name in row.index else None
        )
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            return v
    return default


def _fmt(v, default="–") -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return default
    return fmt_price(float(v))


def _pnl_str(pnl) -> str:
    if pnl is None:
        return ""
    pnl = float(pnl)
    return f" {'🟢' if pnl >= 0 else '🔴'} {pnl:+.1f}%"


_BULL_LABELS = {
    "ema": "EMA9>21", "vwap": "Giá>VWAP", "rsi": "RSI>52",
    "macd": "MACD↑",  "adx": "ADX>20",   "obv": "OBV↑",
    "stoch": "Stoch↑", "candle": "Nến↑",  "vol": "Vol↑",
}

GUIDE = (
    "<b>📖 Hướng dẫn giao dịch theo chỉ báo</b>\n"
    "\n"
    "<b>🟢 Khi nào MUA?</b>\n"
    "• Xu hướng chuyển sang <b>tăng</b> (vào vùng xanh)\n"
    "• Giá đang gần hoặc vừa bật lên từ <b>vùng hỗ trợ</b>\n"
    "• Mua tại <b>giá đóng cửa</b> ngày xuất hiện tín hiệu\n"
    "\n"
    "<b>🔴 Khi nào BÁN / Cắt lỗ?</b>\n"
    "• Xu hướng chuyển sang <b>giảm</b> (ra khỏi vùng xanh) → thoát lệnh\n"
    "• Giá phá thủng <b>vùng hỗ trợ</b> và đóng cửa bên dưới → cắt lỗ\n"

    "<b>🎯 Quản lý vị thế</b>\n"
    "\n"
    "<b>Chốt lời từng phần theo chỉ báo:</b>\n"
    "• Khi giá chạm <b>vùng kháng cự</b> → chốt 40–50% vị thế\n"
    "• Khi BiasNorm bắt đầu yếu dần (giảm liên tiếp) → chốt thêm 30–40%\n"
    "• Phần còn lại (~10–20%) <b>gồng theo xu hướng tăng</b>, chỉ thoát khi xu hướng đổi sang giảm\n"
    "\n"
    "<b>Cắt lỗ từng bước:</b>\n"
    "• Đặt SL ban đầu 1–2% dưới <b>vùng hỗ trợ</b> gần nhất\n"
    "• Khi lời ≥ 10%, dời SL lên điểm hoà vốn (break-even)\n"
    "• Khi lời ≥ 20%, dời SL lên theo hỗ trợ mới để bảo vệ lợi nhuận\n"
    "\n"
    "<b>⚡ Dài hạn vs Ngắn hạn</b>\n"
    "• <b>Dài hạn</b>: ít tín hiệu hơn, lọc nhiễu tốt, phù hợp nắm giữ\n"
    "• <b>Ngắn hạn</b>: nhiều tín hiệu hơn, phù hợp swing trade\n"
    "• Mã xuất hiện ở cả hai → tín hiệu đặc biệt mạnh\n"
    "\n"
    "<i>📌 Chỉ báo kỹ thuật để tham khảo — bạn vẫn là người ra quyết định nhé!</i>"
)

MENU = (
    "<b>🤖 MDAlpha3 Bot — Lệnh</b>\n"
    "\n"
    "<b>📊 Tra cứu</b>\n"
    "<code>/check VHM</code>\n"
    "  Giá, BiasNorm, xu hướng, hỗ trợ/kháng cự, tín hiệu gần nhất, gợi ý SL/TP\n"
    "\n"
    "<code>/top 10</code>\n"
    "  Top N mã vùng xanh theo BiasNorm (mặc định 10)\n"
    "\n"
    "<b>💼 Danh mục</b>\n"
    "<code>/dangiu</code>\n"
    "  Mã đang giữ + ngày mua + P&amp;L hiện tại\n"
    "\n"
    "<b>⏰ Cảnh báo giá</b>\n"
    "<code>/alert VHM 25.5</code>\n"
    "  Bot thông báo khi VHM chạm 25.5\n"
    "\n"
    "<code>/alerts</code>  —  xem tất cả cảnh báo\n"
    "<code>/delalert 3</code>  —  xoá cảnh báo #3\n"
    "\n"
    "Xem hướng dẫn giao dịch: /huongdan"
)


# ─── Command handlers ─────────────────────────────────────────────────────────

def _cmd_check(ticker: str) -> str:
    ticker = ticker.upper()
    df = _scan_df()
    if df.empty or ticker not in df["ticker"].values:
        return f"Không tìm thấy <b>{ticker}</b> trong watchlist."

    r = df[df["ticker"] == ticker].iloc[0]
    is_dual = "long_trend" in df.columns

    bias  = float(_val(r, "bias_norm") or 0)
    close = _val(r, "close")
    b_sc  = int(_val(r, "b_score") or 0)

    def _badge(v) -> str:
        return "✅ Xu hướng tăng" if int(v or 0) == 1 else "🔴 Xu hướng giảm"

    support    = _val(r, "support")
    resistance = _val(r, "resistance")

    if is_dual:
        trend_block = (
            f"Dài hạn : {_badge(_val(r, 'long_trend'))}\n"
            f"Ngắn hạn: {_badge(_val(r, 'short_trend'))}"
        )
        sig_type  = _val(r, "long_last_signal_type") or "–"
        sig_date  = str(_val(r, "long_last_signal_date") or "")[:10]
        sig_price = _val(r, "long_last_signal_price")
        pnl       = _val(r, "long_signal_pnl_pct")
    else:
        trend_block = _badge(_val(r, "trend"))
        sig_type  = _val(r, "last_signal_type") or "–"
        sig_date  = str(_val(r, "last_signal_date") or "")[:10]
        sig_price = _val(r, "last_signal_price")
        pnl       = _val(r, "signal_pnl_pct")

    ht_kc = ""
    if support or resistance:
        ht_kc = f"\nHỗ trợ: {_fmt(support)}  |  Kháng cự: {_fmt(resistance)}"

    sig_block = f"\nTín hiệu: <b>{sig_type}</b> ngày {sig_date} @ {_fmt(sig_price)}"
    if pnl is not None:
        sig_block += f"\nP&L:{_pnl_str(pnl)}"

    parts = [("✅" if r.get(f"bull_{k}") else "❌") + v for k, v in _BULL_LABELS.items()]
    crit = "  " + "  ".join(parts[:5]) + "\n  " + "  ".join(parts[5:])

    # SL đặt 1–2% dưới vùng hỗ trợ; fallback về long_supertrend nếu chưa có support
    sl_base = float(support) if support else _val(r, "long_supertrend", "supertrend")
    sl_tp = ""
    if sl_base and close:
        sl_tp = (
            f"\n\nGợi ý giao dịch:\n"
            f"  SL : {_fmt(float(sl_base) * 0.98)} (1–2% dưới hỗ trợ)\n"
            f"  TP1: {_fmt(float(close) * 1.07)} (+7%)\n"
            f"  TP2: {_fmt(float(close) * 1.15)} (+15%)"
        )

    return (
        f"<b>🔍 {ticker}</b>\n"
        f"Giá: <b>{_fmt(close)}</b> | BiasNorm: <b>{bias:.0f}/100</b> ({bias_label(bias)})\n"
        f"{trend_block}"
        f"{ht_kc}"
        f"{sig_block}\n"
        f"\nBull ({b_sc}/9):\n{crit}"
        f"{sl_tp}"
    )


def _cmd_top(n: int = 10) -> str:
    df = _scan_df()
    if df.empty:
        return "Chưa có dữ liệu scan."

    is_dual = "long_trend" in df.columns
    trend_col = "long_trend" if is_dual else "trend"
    pnl_col   = "long_signal_pnl_pct" if is_dual else "signal_pnl_pct"

    subset = df[df[trend_col] == 1] if trend_col in df.columns else df
    if subset.empty:
        return "Không có mã nào trong vùng xanh hiện tại."

    if "turnover" in subset.columns:
        top = subset.nlargest(min(n, 20), "turnover")
    else:
        top = subset.nlargest(min(n, 20), "bias_norm")

    lines = [f"<b>🏆 Top {len(top)} Vùng Xanh (Thanh khoản cao nhất):</b>"]
    for i, (_, row) in enumerate(top.iterrows(), 1):
        tk = float(row.get("turnover") or 0)
        tk_str = f"{tk/1e9:.1f} tỷ" if tk > 0 else "–"
        lines.append(
            f"  {i}. <b>{row['ticker']}</b>"
            f" | {_fmt(_val(row, 'close'))}"
            f" | TK {tk_str}"
            f"{_pnl_str(_val(row, pnl_col))}"
        )
    return "\n".join(lines)


def _cmd_dangiu() -> str:
    df = _scan_df()
    if df.empty:
        return "Chưa có dữ liệu scan."

    is_dual = "long_trend" in df.columns
    trend_col = "long_trend" if is_dual else "trend"
    pnl_col   = "long_signal_pnl_pct" if is_dual else "signal_pnl_pct"
    date_col  = "long_last_signal_date"  if is_dual else "last_signal_date"
    price_col = "long_last_signal_price" if is_dual else "last_signal_price"
    sig_col   = "long_last_signal_type"  if is_dual else "last_signal_type"

    if trend_col not in df.columns:
        return "Không có dữ liệu trend."

    holding = df[df[trend_col] == 1]
    if sig_col in df.columns:
        holding = holding[holding[sig_col] == "MUA"]
    if holding.empty:
        return "Hiện không có mã nào đang giữ (vùng xanh + tín hiệu MUA)."

    if pnl_col in holding.columns:
        holding = holding.sort_values(pnl_col, ascending=False, na_position="last")

    lines = [f"<b>💼 Đang giữ — {len(holding)} mã:</b>"]
    for _, row in holding.iterrows():
        buy_date  = str(row.get(date_col) or "")[:10]
        buy_price = _val(row, price_col)
        buy_info  = f" | mua {buy_date} @ {_fmt(buy_price)}" if buy_price else ""
        lines.append(
            f"  <b>{row['ticker']}</b>: {_fmt(_val(row, 'close'))}"
            f"{buy_info}{_pnl_str(_val(row, pnl_col))}"
        )
    return "\n".join(lines)


def _cmd_set_alert(chat_id: str, ticker: str, target_str: str) -> str:
    ticker = ticker.upper()
    try:
        target = float(target_str.replace(",", "."))
    except ValueError:
        return "Giá không hợp lệ.\nVí dụ: <code>/alert VHM 25.5</code>"

    df = _scan_df()
    current = None
    if not df.empty and ticker in df["ticker"].values:
        current = _val(df[df["ticker"] == ticker].iloc[0], "close")

    direction = "above" if (current is None or float(current) < target) else "below"

    from scanner.database import save_price_alert
    aid = save_price_alert(chat_id, ticker, target, direction)

    dir_str = "vượt lên ≥" if direction == "above" else "giảm xuống ≤"
    cur_str = f"\n  Giá hiện tại: {_fmt(current)}" if current else ""
    return (
        f"⏰ Cảnh báo #{aid} đã đặt!\n"
        f"  <b>{ticker}</b> khi giá {dir_str} <b>{fmt_price(target)}</b>"
        f"{cur_str}"
    )


def _cmd_list_alerts(chat_id: str) -> tuple[str, dict]:
    """Trả về (text, keyboard) — keyboard có nút xoá từng cảnh báo."""
    from scanner.database import get_price_alerts
    alerts = get_price_alerts(chat_id)
    if not alerts:
        return "Bạn chưa có cảnh báo nào.\nBấm ⏰ Đặt cảnh báo để thêm mới.", _KB_BACK

    lines = [f"<b>⏰ Cảnh báo của bạn ({len(alerts)}):</b>"]
    buttons = []
    for a in alerts:
        arrow = "↑" if a["direction"] == "above" else "↓"
        lines.append(f"  <b>{a['ticker']}</b> {arrow} {fmt_price(float(a['target_price']))}")
        buttons.append([{
            "text": f"🗑 Xoá {a['ticker']} {arrow} {fmt_price(float(a['target_price']))}",
            "callback_data": f"del_{a['id']}",
        }])
    buttons.append([{"text": "🔙 Menu chính", "callback_data": "main_menu"}])

    return "\n".join(lines), {"inline_keyboard": buttons}


def _cmd_delete_alert(chat_id: str, id_str: str) -> str:
    try:
        alert_id = int(id_str)
    except ValueError:
        return "ID không hợp lệ.\nVí dụ: <code>/delalert 3</code>"
    from scanner.database import delete_price_alert
    ok = delete_price_alert(chat_id, alert_id)
    return f"✅ Đã xoá cảnh báo #{alert_id}." if ok else f"Không tìm thấy cảnh báo #{alert_id}."


# ─── Alert checker ────────────────────────────────────────────────────────────

def _check_alerts(token: str) -> None:
    from scanner.database import get_all_active_alerts, mark_alert_triggered
    try:
        df = _scan_df()
        if df.empty:
            return
        price_map = {str(r["ticker"]): _val(r, "close") for _, r in df.iterrows()}
        for a in get_all_active_alerts():
            current = price_map.get(a["ticker"])
            if current is None:
                continue
            current = float(current)
            target  = float(a["target_price"])
            hit = (a["direction"] == "above" and current >= target) or \
                  (a["direction"] == "below" and current <= target)
            if hit:
                dir_str = "vượt lên" if a["direction"] == "above" else "giảm xuống"
                _reply(
                    token, a["chat_id"],
                    f"⏰ <b>Cảnh báo #{a['id']} kích hoạt!</b>\n"
                    f"<b>{a['ticker']}</b> đã {dir_str} mức <b>{fmt_price(target)}</b>\n"
                    f"Giá hiện tại: <b>{fmt_price(current)}</b>",
                )
                mark_alert_triggered(a["id"])
    except Exception as e:
        logger.warning(f"_check_alerts: {e}")


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def _dispatch(token: str, message: dict) -> None:
    chat_id = message["chat"]["id"]
    cid_str = str(chat_id)
    text    = (message.get("text") or "").strip()

    # ── Xử lý state machine (user đang chờ nhập mã/giá) ──────────────────────
    if not text.startswith("/"):
        state = _user_state.pop(cid_str, None)
        if state == "check":
            _reply(token, chat_id, _cmd_check(text.split()[0]), _KB_BACK)
        elif state == "alert":
            parts = text.split()
            if len(parts) >= 2:
                _reply(token, chat_id, _cmd_set_alert(cid_str, parts[0], parts[1]), _KB_BACK)
            else:
                _user_state[cid_str] = "alert"
                _reply(token, chat_id, "Nhập đúng định dạng: <code>VHM 25.5</code>")
        return

    # ── Lệnh slash ────────────────────────────────────────────────────────────
    parts = text.split()
    cmd   = parts[0].lower().split("@")[0]

    if cmd in ("/start", "/help"):
        _send_main_menu(token, chat_id)
    elif cmd == "/huongdan":
        _reply(token, chat_id, GUIDE, _KB_BACK)
    elif cmd == "/check":
        reply = _cmd_check(parts[1]) if len(parts) >= 2 else "Dùng: <code>/check VHM</code>"
        _reply(token, chat_id, reply, _KB_BACK)
    elif cmd == "/top":
        n = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 10
        _reply(token, chat_id, _cmd_top(n), _KB_BACK)
    elif cmd == "/dangiu":
        _reply(token, chat_id, _cmd_dangiu(), _KB_BACK)
    elif cmd == "/alert":
        reply = (
            _cmd_set_alert(cid_str, parts[1], parts[2])
            if len(parts) >= 3
            else "Dùng: <code>/alert VHM 25.5</code>"
        )
        _reply(token, chat_id, reply, _KB_BACK)
    elif cmd == "/alerts":
        text, kb = _cmd_list_alerts(cid_str)
        _reply(token, chat_id, text, kb)
    elif cmd == "/delalert":
        reply = (
            _cmd_delete_alert(cid_str, parts[1])
            if len(parts) >= 2
            else "Dùng: <code>/delalert &lt;id&gt;</code>"
        )
        _reply(token, chat_id, reply, _KB_BACK)
    else:
        _send_main_menu(token, chat_id)


# ─── Main loop ────────────────────────────────────────────────────────────────

def run(token: str | None = None) -> None:
    token = token or TELEGRAM_TOKEN
    if not token:
        raise ValueError("TELEGRAM_TOKEN không được set. Kiểm tra biến môi trường.")

    logger.info("Bot interactive: bắt đầu long-polling loop")
    offset = 0
    last_alert_check = 0.0

    while True:
        try:
            updates = _get_updates(token, offset=offset, timeout=30)
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    if cq := upd.get("callback_query"):
                        _handle_callback(token, cq)
                    elif msg := upd.get("message"):
                        if msg.get("text"):
                            _dispatch(token, msg)
                except Exception as e:
                    logger.warning(f"dispatch error: {e}")

            now = time.time()
            if now - last_alert_check >= 60:
                _check_alerts(token)
                last_alert_check = now

        except KeyboardInterrupt:
            logger.info("Bot dừng.")
            break
        except Exception as e:
            logger.warning(f"Bot loop error: {e}")
            time.sleep(5)
