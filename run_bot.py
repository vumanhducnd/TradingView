"""
Entry point cho bot interactive.
Chạy đồng thời: Telegram long-polling + HTTP health check (cho Render free tier).
UptimeRobot ping /health mỗi 14 phút để giữ service không bị spin down.
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def log_message(self, *args):
        pass  # tắt access log


def _start_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    server.serve_forever()


if __name__ == "__main__":
    threading.Thread(target=_start_health_server, daemon=True).start()
    from scanner.bot_interactive import run
    run()
