"""
Entry point cho bot interactive.
Chạy đồng thời: Telegram long-polling + HTTP health check (cho Render free tier).
UptimeRobot ping /health mỗi 5 phút để giữ service không bị spin down.
Health check trả 503 nếu bot thread chết → Render tự restart.
"""
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

_bot_thread: threading.Thread | None = None


class _HealthHandler(BaseHTTPRequestHandler):
    def _status(self):
        return 503 if (_bot_thread is None or not _bot_thread.is_alive()) else 200

    def do_GET(self):
        code = self._status()
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"Bot thread dead" if code == 503 else b"OK")

    def do_HEAD(self):
        self.send_response(self._status())
        self.end_headers()

    def log_message(self, format, *args):
        print(f"[health] {self.address_string()} {format % args}", flush=True)


def _start_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()


def _run_bot_with_restart():
    """Chạy bot, tự restart nếu crash."""
    from scanner.bot_interactive import run
    while True:
        try:
            run()
        except Exception as e:
            print(f"[run_bot] Bot crashed: {e} — restart sau 10s")
            time.sleep(10)


if __name__ == "__main__":
    threading.Thread(target=_start_health_server, daemon=True).start()

    _bot_thread = threading.Thread(target=_run_bot_with_restart, daemon=False)
    _bot_thread.start()
    _bot_thread.join()
