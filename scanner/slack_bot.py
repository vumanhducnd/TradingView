"""
Slack Incoming Webhook sender.
Set SLACK_WEBHOOK_URL trong .env hoặc GitHub Secrets.
"""

from __future__ import annotations

import json
import os
import urllib.request

from scanner.utils import logger

SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")


def send_slack(text: str) -> bool:
    if not SLACK_WEBHOOK_URL:
        logger.debug("SLACK_WEBHOOK_URL chưa set, bỏ qua")
        return False
    try:
        payload = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            SLACK_WEBHOOK_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception as e:
        logger.warning(f"Slack send failed: {e}")
        return False


def send_flip_alert(
    ticker: str,
    direction: str,      # "buy" | "sell"
    price: float,
    supertrend: float,
    bias_norm: float | None = None,
    time_str: str = "",
) -> None:
    if direction == "buy":
        emoji, label = "🟢", "LẬT TĂNG — XEM XÉT MUA"
    else:
        emoji, label = "🔴", "LẬT GIẢM — XEM XÉT BÁN"

    lines = [
        f"{emoji} *{ticker}* | {label}",
        f"Giá: *{price:,.0f}* | SuperTrend: {supertrend:,.0f}",
    ]
    if bias_norm is not None:
        lines.append(f"BiasNorm: {bias_norm:.0f}/100")
    if time_str:
        lines.append(f"Thời gian: {time_str}")

    send_slack("\n".join(lines))


def send_session_start(n_tickers: int, interval_min: int) -> None:
    send_slack(
        f"📡 *Session Scanner BẮT ĐẦU*\n"
        f"Theo dõi {n_tickers} mã | Quét mỗi {interval_min} phút\n"
        f"Phiên: 09:00 – 15:15 ICT"
    )


def send_session_end(n_flips: int) -> None:
    send_slack(
        f"🔔 *Session Scanner KẾT THÚC* (15:15 ICT)\n"
        f"Tổng tín hiệu trong phiên: {n_flips}"
    )
