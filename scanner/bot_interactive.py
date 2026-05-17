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
import os
import time

import pandas as pd
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from scanner.config import TELEGRAM_TOKEN
from scanner.utils import bias_label, fmt_price, logger

ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "")


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


def _edit(token: str, chat_id: int | str, message_id: int,
          text: str, keyboard: dict | None = None) -> None:
    """Cập nhật tin hiện tại — dùng cho navigation buttons."""
    body: dict = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        body["reply_markup"] = keyboard
    try:
        resp = requests.post(_api(token, "editMessageText"), json=body, timeout=15, verify=False)
        if not resp.ok and "message is not modified" not in resp.text:
            _reply(token, chat_id, text, keyboard)
    except Exception as e:
        logger.warning(f"edit error: {e}")
        _reply(token, chat_id, text, keyboard)


# ─── User preferences ────────────────────────────────────────────────────────

_user_state: dict[str, str] = {}   # chat_id → "check" | "alert"
_user_trend: dict[str, str] = {}   # chat_id → "short" | "long"  (default: "short")


def _trend(cid: str) -> str:
    return _user_trend.get(cid, "short")

def _trend_label(cid: str) -> str:
    return "⚡ Ngắn hạn" if _trend(cid) == "short" else "📈 Dài hạn"


# ─── Keyboard layouts ─────────────────────────────────────────────────────────

def _kb_main(cid: str) -> dict:
    t = _trend(cid)
    short_btn = "⚡ Ngắn hạn ✓" if t == "short" else "⚡ Ngắn hạn"
    long_btn  = "📈 Dài hạn ✓"  if t == "long"  else "📈 Dài hạn"
    rows = [
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
        [
            {"text": short_btn, "callback_data": "trend_short"},
            {"text": long_btn,  "callback_data": "trend_long"},
        ],
    ]
    if _is_admin(cid):
        rows.append([{"text": "👮 Admin Panel", "callback_data": "admin_panel"}])
    return {"inline_keyboard": rows}


_KB_BACK = {
    "inline_keyboard": [[{"text": "🔙 Menu chính", "callback_data": "main_menu"}]]
}

_KB_ADMIN_BACK = {
    "inline_keyboard": [[{"text": "🔙 Admin Panel", "callback_data": "admin_panel"}]]
}


def _kb_top(cid: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Top 5",  "callback_data": "top_5"},
                {"text": "Top 10", "callback_data": "top_10"},
                {"text": "Top 20", "callback_data": "top_20"},
            ],
            [{"text": "🔙 Menu chính", "callback_data": "main_menu"}],
        ]
    }


def _kb_admin_menu(pending: int, active: int, blocked: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": f"⏳ Chờ duyệt ({pending})", "callback_data": "adm_list_pending_0"},
                {"text": f"✅ Active ({active})",     "callback_data": "adm_list_active_0"},
            ],
            [
                {"text": f"🚫 Đã chặn ({blocked})",  "callback_data": "adm_list_blocked_0"},
                {"text": "👥 Tất cả",                 "callback_data": "adm_list_all_0"},
            ],
            [{"text": "📢 Quản lý Channels",          "callback_data": "admin_channels"}],
            [{"text": "🔙 Menu chính",                 "callback_data": "main_menu"}],
        ]
    }


def _kb_user_actions(target_id: str, current_status: str) -> dict:
    """Nút hành động cho từng user tùy theo status hiện tại."""
    if current_status == "pending":
        rows = [
            [
                {"text": "✅ Kích hoạt",       "callback_data": f"sel_apv_{target_id}"},
                {"text": "⏱ Dùng thử 3 ngày", "callback_data": f"trial_{target_id}"},
            ],
            [{"text": "🚫 Chặn", "callback_data": f"block_{target_id}"}],
        ]
    elif current_status in ("active", "trial"):
        rows = [[{"text": "🚫 Chặn", "callback_data": f"block_{target_id}"}]]
    elif current_status == "blocked":
        rows = [
            [
                {"text": "✅ Kích hoạt",       "callback_data": f"sel_apv_{target_id}"},
                {"text": "⏱ Dùng thử 3 ngày", "callback_data": f"trial_{target_id}"},
            ],
        ]
    else:
        rows = []
    rows.append([{"text": "🔙 Admin Panel", "callback_data": "admin_panel"}])
    return {"inline_keyboard": rows}


def _kb_approve_duration(target_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "♾ Vĩnh viễn",  "callback_data": f"apv_{target_id}_0"},
                {"text": "📅 30 ngày",   "callback_data": f"apv_{target_id}_30"},
            ],
            [
                {"text": "📅 90 ngày",   "callback_data": f"apv_{target_id}_90"},
                {"text": "📅 180 ngày",  "callback_data": f"apv_{target_id}_180"},
            ],
            [{"text": "📅 1 năm",        "callback_data": f"apv_{target_id}_365"}],
            [{"text": "🔙 Admin Panel",  "callback_data": "admin_panel"}],
        ]
    }


# ─── Access control ───────────────────────────────────────────────────────────

def _is_admin(chat_id: str) -> bool:
    return bool(ADMIN_CHAT_ID) and str(chat_id) == str(ADMIN_CHAT_ID)


def _check_access(chat_id: str) -> str | None:
    """Trả về status: 'active' | 'trial' | 'pending' | 'blocked' | 'expired' | None."""
    if not ADMIN_CHAT_ID:
        return "active"
    if _is_admin(chat_id):
        return "active"
    from scanner.database import get_bot_user, set_user_status
    user = get_bot_user(chat_id)
    if not user:
        return None
    if user["status"] in ("trial", "active"):
        expires = user.get("trial_expires_at")
        if expires:
            from datetime import timezone, datetime as _dt
            now = _dt.now(timezone.utc)
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                set_user_status(chat_id, "blocked")
                return "expired"
    return user["status"]


def _send_registration_prompt(token: str, chat_id: int | str) -> None:
    try:
        requests.post(
            _api(token, "sendMessage"),
            json={
                "chat_id": chat_id,
                "text": (
                    "👋 Chào mừng đến với <b>MDAlpha3 Bot</b>!\n\n"
                    "Để sử dụng bot, vui lòng xác thực số điện thoại của bạn.\n"
                    "Bấm nút bên dưới để gửi số điện thoại:"
                ),
                "parse_mode": "HTML",
                "reply_markup": {
                    "keyboard": [[{
                        "text": "📱 Gửi số điện thoại để đăng ký",
                        "request_contact": True,
                    }]],
                    "resize_keyboard": True,
                    "one_time_keyboard": True,
                },
            },
            timeout=15,
            verify=False,
        )
    except Exception as e:
        logger.warning(f"_send_registration_prompt error: {e}")


def _remove_keyboard(token: str, chat_id: int | str, text: str) -> None:
    try:
        requests.post(
            _api(token, "sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "reply_markup": {"remove_keyboard": True},
            },
            timeout=15,
            verify=False,
        )
    except Exception as e:
        logger.warning(f"_remove_keyboard error: {e}")


def _handle_contact(token: str, message: dict) -> None:
    """Xử lý khi user gửi số điện thoại."""
    contact   = message["contact"]
    chat_id   = str(message["chat"]["id"])
    phone     = contact.get("phone_number", "")
    full_name = " ".join(filter(None, [
        contact.get("first_name", ""),
        contact.get("last_name", ""),
    ]))
    username = message["chat"].get("username", "")

    from scanner.database import upsert_bot_user
    upsert_bot_user(chat_id, phone, full_name, username)

    _remove_keyboard(
        token, chat_id,
        "✅ Đã nhận thông tin!\n"
        "⏳ Tài khoản đang chờ admin duyệt. Bạn sẽ được thông báo khi được kích hoạt.",
    )

    # Thông báo admin
    if ADMIN_CHAT_ID:
        try:
            requests.post(
                _api(token, "sendMessage"),
                json={
                    "chat_id": ADMIN_CHAT_ID,
                    "text": (
                        f"🔔 <b>Người dùng mới đăng ký</b>\n"
                        f"Tên: {full_name}\n"
                        f"SĐT: <code>{phone}</code>\n"
                        f"Username: @{username or '–'}\n"
                        f"Chat ID: <code>{chat_id}</code>"
                    ),
                    "parse_mode": "HTML",
                    "reply_markup": {
                        "inline_keyboard": [[
                            {"text": "✅ Duyệt", "callback_data": f"approve_{chat_id}"},
                            {"text": "🚫 Chặn",  "callback_data": f"block_{chat_id}"},
                        ]]
                    },
                },
                timeout=15,
                verify=False,
            )
        except Exception as e:
            logger.warning(f"notify admin failed: {e}")


# ─── Telegram API helpers ─────────────────────────────────────────────────────

_last_callback: dict[str, float] = {}  # chat_id → timestamp
_RATE_LIMIT = 2.0  # giây tối thiểu giữa 2 lần bấm


def _answer_callback(token: str, callback_id: str, text: str = "", alert: bool = False) -> None:
    try:
        body: dict = {"callback_query_id": callback_id}
        if text:
            body["text"] = text
            body["show_alert"] = alert
        requests.post(_api(token, "answerCallbackQuery"), json=body, timeout=10, verify=False)
    except Exception:
        pass


def _is_rate_limited(chat_id: str, callback_id: str, token: str) -> bool:
    """Trả về True nếu user bấm quá nhanh — đã gửi toast thông báo."""
    now = time.time()
    last = _last_callback.get(chat_id, 0)
    if now - last < _RATE_LIMIT:
        _answer_callback(token, callback_id, "⏳ Vui lòng chờ một chút!", alert=True)
        return True
    _last_callback[chat_id] = now
    return False


def _send_main_menu(token: str, chat_id: int | str) -> None:
    cid = str(chat_id)
    _reply(
        token, chat_id,
        f"👋 Chào mừng đến với <b>MDAlpha3 Bot</b>!\n"
        f"Đang xem: <b>{_trend_label(cid)}</b>\n"
        f"Chọn tính năng bạn muốn dùng:",
        _kb_main(cid),
    )


_PAGE_SIZE = 8

_FILTER_LABEL = {
    "pending": "⏳ Chờ duyệt",
    "active":  "✅ Active",
    "blocked": "🚫 Đã chặn",
    "all":     "👥 Tất cả",
}


def _send_admin_user_list(token: str, chat_id: int | str, f_key: str, page: int, message_id: int | None = None) -> None:
    from scanner.database import get_users_by_status, get_all_bot_users
    users = get_all_bot_users() if f_key == "all" else get_users_by_status(f_key)
    label = _FILTER_LABEL.get(f_key, f_key)
    total = len(users)

    if not users:
        _reply(token, chat_id, f"Không có user nào trong mục {label}.", _KB_ADMIN_BACK)
        return

    total_pages = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    page = max(0, min(page, total_pages - 1))
    chunk = users[page * _PAGE_SIZE:(page + 1) * _PAGE_SIZE]

    # Danh sách text
    icons = {"active": "✅", "pending": "⏳", "blocked": "🚫", "trial": "⏱"}
    lines = [f"<b>{label} — {total} user (trang {page+1}/{total_pages}):</b>"]
    for u in chunk:
        ic = icons.get(u["status"], "❓")
        trial_info = ""
        if u.get("trial_expires_at"):
            exp = u["trial_expires_at"]
            trial_info = f" (hết {exp.strftime('%d/%m/%Y')})"
        lines.append(
            f"{ic} <b>{u['full_name'] or '–'}</b> | "
            f"<code>{u['phone'] or '–'}</code>{trial_info}"
        )

    # Keyboard: nút hành động theo từng user
    user_rows = []
    for u in chunk:
        row = []
        if u["status"] == "pending":
            row = [
                {"text": f"✅ {(u['full_name'] or u['chat_id'])[:15]}", "callback_data": f"approve_{u['chat_id']}"},
                {"text": "🚫", "callback_data": f"block_{u['chat_id']}"},
            ]
        elif u["status"] == "active":
            row = [{"text": f"🚫 Chặn {(u['full_name'] or u['chat_id'])[:20]}", "callback_data": f"block_{u['chat_id']}"}]
        elif u["status"] == "blocked":
            row = [{"text": f"✅ Bỏ chặn {(u['full_name'] or u['chat_id'])[:18]}", "callback_data": f"approve_{u['chat_id']}"}]
        if row:
            user_rows.append(row)

    # Phân trang
    nav = []
    if page > 0:
        nav.append({"text": "◄ Trước", "callback_data": f"adm_list_{f_key}_{page-1}"})
    nav.append({"text": f"{page+1}/{total_pages}", "callback_data": "admin_panel"})
    if page < total_pages - 1:
        nav.append({"text": "Tiếp ►", "callback_data": f"adm_list_{f_key}_{page+1}"})

    kb = {"inline_keyboard": user_rows + [nav, [{"text": "🔙 Admin Panel", "callback_data": "admin_panel"}]]}
    if message_id:
        _edit(token, chat_id, message_id, "\n".join(lines), kb)
    else:
        _reply(token, chat_id, "\n".join(lines), kb)


def _handle_callback(token: str, cq: dict) -> None:
    chat_id    = cq["message"]["chat"]["id"]
    message_id = cq["message"]["message_id"]
    cid_str    = str(chat_id)
    data       = cq.get("data", "")

    if _is_rate_limited(cid_str, cq["id"], token):
        return
    _answer_callback(token, cq["id"])

    # ── Access control cho callback (trừ main_menu/guide để user pending vẫn thấy) ──
    _PUBLIC = {"main_menu", "guide"}
    if data not in _PUBLIC and not _is_admin(cid_str):
        status = _check_access(cid_str)
        if status is None:
            _send_registration_prompt(token, chat_id)
            return
        if status == "pending":
            _reply(token, chat_id, "⏳ Tài khoản đang chờ admin duyệt.")
            return
        if status in ("blocked", "expired"):
            _reply(token, chat_id, "🚫 Tài khoản không còn hiệu lực.")
            return

    style = _trend(cid_str)

    if data == "main_menu":
        _user_state.pop(cid_str, None)
        _edit(token, chat_id, message_id,
              f"👋 Chào mừng đến với <b>MDAlpha3 Bot</b>!\n"
              f"Đang xem: <b>{_trend_label(cid_str)}</b>\n"
              f"Chọn tính năng bạn muốn dùng:",
              _kb_main(cid_str))

    elif data in ("trend_short", "trend_long"):
        _user_trend[cid_str] = "short" if data == "trend_short" else "long"
        _edit(token, chat_id, message_id,
              f"👋 Chào mừng đến với <b>MDAlpha3 Bot</b>!\n"
              f"Đang xem: <b>{_trend_label(cid_str)}</b>\n"
              f"Chọn tính năng bạn muốn dùng:",
              _kb_main(cid_str))

    elif data == "guide":
        _reply(token, chat_id, GUIDE, _KB_BACK)  # content dài → tin mới

    elif data == "input_check":
        _user_state[cid_str] = "check"
        _edit(token, chat_id, message_id,
            f"🔍 Nhập mã cổ phiếu (VD: <code>VHM</code>)\n"
            f"<i>Đang xem: {_trend_label(cid_str)}</i>")

    elif data == "top_menu":
        _edit(token, chat_id, message_id,
            f"Chọn số lượng mã muốn xem:\n<i>Đang xem: {_trend_label(cid_str)}</i>",
            _kb_top(cid_str))

    elif data in ("top_5", "top_10", "top_20"):
        n = int(data.split("_")[1])
        _edit(token, chat_id, message_id, _cmd_top(n, style), _KB_BACK)

    elif data == "dangiu":
        try:
            _reply(token, chat_id, _cmd_dangiu(style), _KB_BACK)
        except Exception as e:
            logger.warning(f"dangiu error: {e}")
            _reply(token, chat_id, "❌ Lỗi tải dữ liệu. Vui lòng thử lại.", _KB_BACK)

    elif data == "input_alert":
        _user_state[cid_str] = "alert"
        _reply(
            token, chat_id,
            "⏰ <b>Đặt cảnh báo giá</b>\n\n"
            "Nhập mã và giá mục tiêu:\n"
            "<code>VHM 25.5</code>\n\n"
            "Bot tự xác định hướng:\n"
            "  • Giá đặt <b>cao hơn</b> giá hiện tại → cảnh báo khi <b>tăng lên</b>\n"
            "  • Giá đặt <b>thấp hơn</b> giá hiện tại → cảnh báo khi <b>giảm xuống</b>",
        )

    elif data == "alerts":
        text, kb = _cmd_list_alerts(cid_str)
        _reply(token, chat_id, text, kb)

    elif data.startswith("del_"):
        aid = data[4:]
        reply = _cmd_delete_alert(cid_str, aid)
        text, kb = _cmd_list_alerts(cid_str)
        _reply(token, chat_id, reply)
        _reply(token, chat_id, text, kb)

    # ── Admin Panel ───────────────────────────────────────────────────────────
    elif data == "admin_panel" and _is_admin(cid_str):
        from scanner.database import get_users_by_status
        p = len(get_users_by_status("pending"))
        a = len(get_users_by_status("active"))
        b = len(get_users_by_status("blocked"))
        _edit(token, chat_id, message_id,
            f"<b>👮 Admin Panel</b>\n"
            f"⏳ Chờ duyệt: <b>{p}</b>  |  ✅ Active: <b>{a}</b>  |  🚫 Chặn: <b>{b}</b>",
            _kb_admin_menu(p, a, b))

    elif data.startswith("adm_list_") and _is_admin(cid_str):
        parts_d = data.split("_")
        f_key   = parts_d[2]
        page    = int(parts_d[3]) if len(parts_d) > 3 else 0
        _send_admin_user_list(token, chat_id, f_key, page, message_id)

    # ── Admin: chọn thời hạn kích hoạt ───────────────────────────────────────
    elif data.startswith("sel_apv_") and _is_admin(cid_str):
        target_id = data[8:]
        from scanner.database import get_bot_user
        u = get_bot_user(target_id) or {}
        name = u.get("full_name") or target_id
        _edit(token, chat_id, message_id,
            f"Kích hoạt <b>{name}</b> trong bao lâu?",
            _kb_approve_duration(target_id))

    elif data.startswith("apv_") and _is_admin(cid_str):
        # format: apv_<chat_id>_<days>
        parts_d = data.split("_", 2)   # ['apv', chat_id, days]  nhưng chat_id có thể có '_'
        days_str = parts_d[-1]
        target_id = "_".join(parts_d[1:-1])
        days = int(days_str)
        from scanner.database import set_user_status, get_bot_user
        ok = set_user_status(target_id, "active", trial_days=days)
        if ok:
            u = get_bot_user(target_id) or {}
            name = u.get("full_name") or target_id
            dur = "vĩnh viễn" if days == 0 else f"{days} ngày"
            _reply(token, chat_id, f"✅ Đã kích hoạt <b>{name}</b> — {dur}")
            msg = (
                f"🎉 Tài khoản đã được kích hoạt <b>{dur}</b>!\nGõ /start để bắt đầu."
                if days == 0 else
                f"🎉 Tài khoản được kích hoạt <b>{days} ngày</b>!\n"
                f"Gõ /start để bắt đầu.\n\n<i>Hết hạn sau {days} ngày.</i>"
            )
            _reply(token, target_id, msg)
        else:
            _reply(token, chat_id, "Không tìm thấy user.")

    # ── Admin: dùng thử ───────────────────────────────────────────────────────
    # ── Channel management ────────────────────────────────────────────────────
    elif data == "admin_channels" and _is_admin(cid_str):
        text, kb = _cmd_list_channels()
        _edit(token, chat_id, message_id, text, kb)

    elif data.startswith("addch_") and _is_admin(cid_str):
        # format: addch_<style>_<channel_id>
        parts_d  = data.split("_", 2)
        style    = parts_d[1]          # long | short
        ch_id    = parts_d[2]
        pending  = _pending_channel.pop(cid_str, {})
        title    = pending.get("title", ch_id)
        from scanner.database import add_bot_channel
        add_bot_channel(ch_id, title, style)
        style_label = "📈 Dài hạn" if style == "long" else "⚡ Ngắn hạn"
        _reply(token, chat_id,
            f"✅ Đã thêm channel <b>{title}</b> → {style_label}\n"
            f"Bot sẽ gửi báo cáo vào channel này từ lần tới.")
        text, kb = _cmd_list_channels()
        _reply(token, chat_id, text, kb)

    elif data.startswith("chtoggle_") and _is_admin(cid_str):
        ch_id = data[9:]
        from scanner.database import get_bot_channels, set_channel_active
        chs = [c for c in get_bot_channels(active_only=False) if c["chat_id"] == ch_id]
        if chs:
            new_state = not chs[0]["is_active"]
            set_channel_active(ch_id, new_state)
            state_txt = "bật" if new_state else "tắt"
            _reply(token, chat_id, f"{'▶' if new_state else '⏸'} Đã {state_txt} channel.")
        text, kb = _cmd_list_channels()
        _reply(token, chat_id, text, kb)

    elif data.startswith("chdelete_") and _is_admin(cid_str):
        ch_id = data[9:]
        from scanner.database import delete_bot_channel
        delete_bot_channel(ch_id)
        _reply(token, chat_id, "🗑 Đã xoá channel.")
        text, kb = _cmd_list_channels()
        _reply(token, chat_id, text, kb)

    elif data.startswith("trial_") and _is_admin(cid_str):
        target_id = data[6:]
        from scanner.database import set_user_status, get_bot_user
        ok = set_user_status(target_id, "trial", trial_days=3)
        if ok:
            u = get_bot_user(target_id) or {}
            name = u.get("full_name") or target_id
            _reply(token, chat_id, f"⏱ Đã cấp dùng thử 3 ngày: <b>{name}</b>")
            _reply(token, target_id,
                "🎉 Tài khoản được dùng thử <b>3 ngày</b>!\n"
                "Gõ /start để bắt đầu.\n\n"
                "<i>Sau 3 ngày hãy liên hệ admin để kích hoạt đầy đủ.</i>")
        else:
            _reply(token, chat_id, "Không tìm thấy user.")

    # ── Admin: duyệt / chặn user ──────────────────────────────────────────────
    elif data.startswith("approve_") or data.startswith("block_"):
        if not _is_admin(cid_str):
            return
        action, target_id = data.split("_", 1)
        from scanner.database import set_user_status, get_bot_user
        ok = set_user_status(target_id, "active" if action == "approve" else "blocked")
        if ok:
            u = get_bot_user(target_id) or {}
            name = u.get("full_name") or target_id
            if action == "approve":
                _reply(token, chat_id, f"✅ Đã kích hoạt tài khoản: <b>{name}</b>")
                _reply(token, target_id,
                    "🎉 Tài khoản của bạn đã được kích hoạt!\nGõ /start để bắt đầu.")
            else:
                _reply(token, chat_id, f"🚫 Đã chặn tài khoản: <b>{name}</b>")
        else:
            _reply(token, chat_id, "Không tìm thấy user.")


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

def _cmd_check(ticker: str, style: str = "short") -> str:
    ticker = ticker.upper()
    df = _scan_df()
    if df.empty or ticker not in df["ticker"].values:
        return f"Không tìm thấy <b>{ticker}</b> trong watchlist."

    r = df[df["ticker"] == ticker].iloc[0]
    p = f"{style}_"  # "long_" hoặc "short_"
    style_label = "⚡ Ngắn hạn" if style == "short" else "📈 Dài hạn"

    bias  = float(_val(r, "bias_norm") or 0)
    close = _val(r, "close")
    b_sc  = int(_val(r, "b_score") or 0)

    def _badge(v) -> str:
        return "✅ Xu hướng tăng" if int(v or 0) == 1 else "🔴 Xu hướng giảm"

    trend_val  = _val(r, f"{p}trend", "trend")
    st_val     = _val(r, f"{p}supertrend", "supertrend")
    sig_type   = _val(r, f"{p}last_signal_type",  "last_signal_type")  or "–"
    sig_date   = str(_val(r, f"{p}last_signal_date",  "last_signal_date")  or "")[:10]
    sig_price  = _val(r, f"{p}last_signal_price", "last_signal_price")
    pnl        = _val(r, f"{p}signal_pnl_pct",    "signal_pnl_pct")
    support    = _val(r, "support")
    resistance = _val(r, "resistance")

    trend_block = f"{style_label}: {_badge(trend_val)}"
    ht_kc = f"\nHỗ trợ: {_fmt(support)}  |  Kháng cự: {_fmt(resistance)}" if (support or resistance) else ""

    sig_block = f"\nTín hiệu: <b>{sig_type}</b> ngày {sig_date} @ {_fmt(sig_price)}"
    if pnl is not None:
        sig_block += f"\nP&L:{_pnl_str(pnl)}"

    parts = [("✅" if r.get(f"bull_{k}") else "❌") + v for k, v in _BULL_LABELS.items()]
    crit = "  " + "  ".join(parts[:5]) + "\n  " + "  ".join(parts[5:])

    sl_base = float(support) if support else (float(st_val) if st_val else None)
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


def _cmd_top(n: int = 10, style: str = "short") -> str:
    df = _scan_df()
    if df.empty:
        return "Chưa có dữ liệu scan."

    p = f"{style}_"
    style_label = "⚡ Ngắn hạn" if style == "short" else "📈 Dài hạn"
    trend_col = f"{p}trend" if f"{p}trend" in df.columns else "trend"
    pnl_col   = f"{p}signal_pnl_pct" if f"{p}signal_pnl_pct" in df.columns else "signal_pnl_pct"

    subset = df[df[trend_col] == 1] if trend_col in df.columns else df
    if subset.empty:
        return f"Không có mã nào trong vùng xanh ({style_label})."

    top = subset.nlargest(min(n, 20), "turnover") if "turnover" in subset.columns \
          else subset.nlargest(min(n, 20), "bias_norm")

    lines = [f"<b>🏆 Top {len(top)} Vùng Xanh — {style_label} (Thanh khoản cao nhất):</b>"]
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


def _cmd_dangiu(style: str = "short") -> str:
    df = _scan_df()
    if df.empty:
        return "Chưa có dữ liệu scan."

    p = f"{style}_"
    style_label = "⚡ Ngắn hạn" if style == "short" else "📈 Dài hạn"
    trend_col = f"{p}trend" if f"{p}trend" in df.columns else "trend"
    pnl_col   = f"{p}signal_pnl_pct"   if f"{p}signal_pnl_pct"   in df.columns else "signal_pnl_pct"
    date_col  = f"{p}last_signal_date"  if f"{p}last_signal_date"  in df.columns else "last_signal_date"
    price_col = f"{p}last_signal_price" if f"{p}last_signal_price" in df.columns else "last_signal_price"
    sig_col   = f"{p}last_signal_type"  if f"{p}last_signal_type"  in df.columns else "last_signal_type"

    if trend_col not in df.columns:
        return "Không có dữ liệu trend."

    holding = df[df[trend_col] == 1].copy()
    if holding.empty:
        return f"Hiện không có mã nào trong vùng xanh ({style_label})."

    if pnl_col in holding.columns:
        holding = holding.sort_values(pnl_col, ascending=False, na_position="last")

    lines = [f"<b>💼 Đang giữ — {style_label} — {len(holding)} mã:</b>"]
    for _, row in holding.iterrows():
        sig       = str(row.get(sig_col) or "").strip()
        buy_date  = str(row.get(date_col) or "")[:10]
        buy_price = _val(row, price_col)
        buy_info  = f" | từ {buy_date} @ {_fmt(buy_price)}" if buy_price else ""
        mua_tag   = " 🟢" if sig == "MUA" else ""
        lines.append(
            f"  <b>{row['ticker']}</b>{mua_tag}"
            f": {_fmt(_val(row, 'close'))}{buy_info}{_pnl_str(_val(row, pnl_col))}"
        )
    lines.append("\n<i>🟢 = có tín hiệu MUA xác nhận</i>")
    return "\n".join(lines)


def _cmd_set_alert(chat_id: str, ticker: str, target_str: str) -> str:
    ticker = ticker.upper()
    try:
        target = float(target_str.replace(",", "."))
    except ValueError:
        return "Giá không hợp lệ.\nVí dụ: <code>/alert VHM 25.5</code>"

    from scanner.database import save_price_alert, get_price_alerts
    existing = get_price_alerts(chat_id)
    if len(existing) >= 5:
        return "⚠️ Bạn đã đặt tối đa 5 cảnh báo.\nXoá bớt trước khi thêm mới."

    df = _scan_df()
    current = None
    if not df.empty and ticker in df["ticker"].values:
        current = _val(df[df["ticker"] == ticker].iloc[0], "close")

    direction = "above" if (current is None or float(current) < target) else "below"
    aid = save_price_alert(chat_id, ticker, target, direction)

    dir_str = "📈 tăng lên ≥" if direction == "above" else "📉 giảm xuống ≤"
    cur_str = f"\n  Giá hiện tại: {_fmt(current)}" if current else ""
    return (
        f"✅ Cảnh báo #{aid} đã đặt!\n"
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

    # ── Kiểm tra quyền truy cập ───────────────────────────────────────────────
    status = _check_access(cid_str)
    if status is None:
        _send_registration_prompt(token, chat_id)
        return
    if status == "pending":
        _reply(token, chat_id,
            "⏳ Tài khoản đang chờ admin duyệt.\nBạn sẽ được thông báo khi được kích hoạt.")
        return
    if status == "expired":
        _reply(token, chat_id,
            "⏰ Thời gian dùng thử đã hết.\n"
            "Liên hệ admin để được kích hoạt tài khoản đầy đủ.")
        return
    if status == "blocked":
        _reply(token, chat_id, "🚫 Tài khoản của bạn đã bị chặn.")
        return

    # ── Xử lý state machine (user đang chờ nhập mã/giá) ──────────────────────
    if not text.startswith("/"):
        state = _user_state.pop(cid_str, None)
        if state == "check":
            _reply(token, chat_id, _cmd_check(text.split()[0], _trend(cid_str)), _KB_BACK)
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

    # ── Admin commands ────────────────────────────────────────────────────────
    elif cmd == "/users" and _is_admin(cid_str):
        from scanner.database import get_all_bot_users
        users = get_all_bot_users()
        if not users:
            _reply(token, chat_id, "Chưa có user nào.")
            return
        lines = [f"<b>👥 Danh sách user ({len(users)}):</b>"]
        for u in users:
            icon = {"active": "✅", "pending": "⏳", "blocked": "🚫"}.get(u["status"], "❓")
            lines.append(
                f"{icon} <b>{u['full_name'] or '–'}</b> | {u['phone'] or '–'}"
                f" | <code>{u['chat_id']}</code>"
            )
        _reply(token, chat_id, "\n".join(lines))

    elif cmd == "/pending" and _is_admin(cid_str):
        from scanner.database import get_users_by_status
        users = get_users_by_status("pending")
        if not users:
            _reply(token, chat_id, "Không có user nào đang chờ duyệt.")
            return
        for u in users:
            requests.post(
                _api(token, "sendMessage"),
                json={
                    "chat_id": chat_id,
                    "text": (
                        f"⏳ <b>{u['full_name'] or '–'}</b>\n"
                        f"SĐT: <code>{u['phone'] or '–'}</code>\n"
                        f"@{u['username'] or '–'} | <code>{u['chat_id']}</code>"
                    ),
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": [[
                        {"text": "✅ Duyệt", "callback_data": f"approve_{u['chat_id']}"},
                        {"text": "🚫 Chặn",  "callback_data": f"block_{u['chat_id']}"},
                    ]]},
                },
                timeout=15, verify=False,
            )

    elif cmd == "/approve" and _is_admin(cid_str) and len(parts) >= 2:
        from scanner.database import set_user_status, get_bot_user
        target = parts[1]
        ok = set_user_status(target, "active")
        if ok:
            u = get_bot_user(target) or {}
            _reply(token, chat_id, f"✅ Đã kích hoạt: <b>{u.get('full_name', target)}</b>")
            _reply(token, target, "🎉 Tài khoản đã được kích hoạt! Gõ /start để bắt đầu.")
        else:
            _reply(token, chat_id, "Không tìm thấy user.")

    elif cmd == "/block" and _is_admin(cid_str) and len(parts) >= 2:
        from scanner.database import set_user_status, get_bot_user
        target = parts[1]
        ok = set_user_status(target, "blocked")
        if ok:
            u = get_bot_user(target) or {}
            _reply(token, chat_id, f"🚫 Đã chặn: <b>{u.get('full_name', target)}</b>")
        else:
            _reply(token, chat_id, "Không tìm thấy user.")

    elif cmd == "/adduser" and _is_admin(cid_str):
        # /adduser <chat_id> <phone> <ten>
        # VD: /adduser 123456789 0912345678 Nguyen Van A
        if len(parts) < 3:
            _reply(token, chat_id,
                "Dùng: <code>/adduser &lt;chat_id&gt; &lt;phone&gt; &lt;tên&gt;</code>\n"
                "VD: <code>/adduser 123456789 0912345678 Nguyen Van A</code>")
        else:
            target_id = parts[1]
            phone     = parts[2]
            name      = " ".join(parts[3:]) if len(parts) > 3 else "Test User"
            from scanner.database import upsert_bot_user
            upsert_bot_user(target_id, phone, name)
            _reply(token, chat_id,
                f"✅ Đã tạo user pending:\n"
                f"  ID: <code>{target_id}</code>\n"
                f"  SĐT: <code>{phone}</code>\n"
                f"  Tên: {name}\n\n"
                f"Vào Admin Panel → Chờ duyệt để test duyệt/chặn.",
                _KB_ADMIN_BACK)

    elif cmd == "/block_sdt" and _is_admin(cid_str) and len(parts) >= 2:
        from scanner.database import get_user_by_phone, set_user_status
        phone = parts[1]
        u = get_user_by_phone(phone)
        if not u:
            _reply(token, chat_id, f"Không tìm thấy user với SĐT: <code>{phone}</code>")
        else:
            set_user_status(u["chat_id"], "blocked")
            _reply(token, chat_id,
                f"🚫 Đã chặn: <b>{u.get('full_name', '–')}</b>\n"
                f"SĐT: <code>{u['phone']}</code> | ID: <code>{u['chat_id']}</code>"
            )

    # ── User commands ─────────────────────────────────────────────────────────
    elif cmd == "/check":
        reply = _cmd_check(parts[1], _trend(cid_str)) if len(parts) >= 2 else "Dùng: <code>/check VHM</code>"
        _reply(token, chat_id, reply, _KB_BACK)
    elif cmd == "/top":
        n = int(parts[1]) if len(parts) >= 2 and parts[1].isdigit() else 10
        _reply(token, chat_id, _cmd_top(n, _trend(cid_str)), _KB_BACK)
    elif cmd == "/dangiu":
        _reply(token, chat_id, _cmd_dangiu(_trend(cid_str)), _KB_BACK)
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

# ─── Channel management ───────────────────────────────────────────────────────

_pending_channel: dict[str, dict] = {}  # chat_id → {channel_id, title} chờ chọn style


def _handle_forward_channel(token: str, message: dict) -> None:
    """Admin forward tin từ channel → bot hỏi style."""
    cid = str(message["chat"]["id"])

    # Hỗ trợ cả Telegram API cũ (forward_from_chat) và mới (forward_origin)
    fwd = message.get("forward_from_chat") or message.get("forward_origin", {}).get("chat", {})
    if not fwd:
        _reply(token, cid, "⚠️ Không đọc được thông tin channel. Thử forward tin khác.")
        return

    ch_id = str(fwd.get("id", ""))
    title = fwd.get("title", ch_id)
    if not ch_id:
        _reply(token, cid, "⚠️ Không lấy được channel ID.")
        return

    _pending_channel[cid] = {"channel_id": ch_id, "title": title}
    _reply(token, cid,
        f"📢 Phát hiện channel: <b>{title}</b>\n"
        f"ID: <code>{ch_id}</code>\n\n"
        f"Channel này nhận loại báo cáo nào?",
        {"inline_keyboard": [
            [
                {"text": "📈 Dài hạn",  "callback_data": f"addch_long_{ch_id}"},
                {"text": "⚡ Ngắn hạn", "callback_data": f"addch_short_{ch_id}"},
            ],
            [{"text": "❌ Huỷ", "callback_data": "admin_channels"}],
        ]})


def _cmd_list_channels() -> tuple[str, dict]:
    from scanner.database import get_bot_channels
    channels = get_bot_channels(active_only=False)
    if not channels:
        return (
            "Chưa có channel nào.\n\n"
            "Forward một tin nhắn từ channel vào đây để thêm.",
            _KB_ADMIN_BACK
        )
    lines = [f"<b>📢 Danh sách channel ({len(channels)}):</b>"]
    rows  = []
    for c in channels:
        icon  = "✅" if c["is_active"] else "⏸"
        style = "📈 DH" if c["style"] == "long" else "⚡ NH"
        lines.append(f"{icon} {style} | <b>{c['title'] or c['chat_id']}</b>")
        toggle_text = "⏸ Tắt" if c["is_active"] else "▶ Bật"
        rows.append([
            {"text": f"{toggle_text} {(c['title'] or '')[:15]}", "callback_data": f"chtoggle_{c['chat_id']}"},
            {"text": "🗑 Xoá", "callback_data": f"chdelete_{c['chat_id']}"},
        ])
    rows.append([{"text": "🔙 Admin Panel", "callback_data": "admin_panel"}])
    return "\n".join(lines), {"inline_keyboard": rows}


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
                        if msg.get("contact"):
                            _handle_contact(token, msg)
                        elif _is_admin(str(msg["chat"]["id"])) and (
                            msg.get("forward_from_chat") or
                            msg.get("forward_origin", {}).get("type") == "channel"
                        ):
                            _handle_forward_channel(token, msg)
                        elif msg.get("text"):
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
