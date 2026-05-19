import logging
import ssl
import sys
import threading
import time
import urllib3
from datetime import date, datetime, timedelta, timezone

# Bypass SSL certificate verification on Windows (local dev only)
# GitHub Actions (Linux) không bị ảnh hưởng vì dùng Linux cert store
ssl._create_default_https_context = ssl._create_unverified_context
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Patch requests.Session để mọi thư viện dùng requests đều bỏ qua SSL
try:
    import requests
    _orig_request = requests.Session.request
    def _no_verify_request(self, method, url, **kwargs):
        kwargs.setdefault("verify", False)
        return _orig_request(self, method, url, **kwargs)
    requests.Session.request = _no_verify_request
except Exception:
    pass


_ICT = timezone(timedelta(hours=7))


class _ICTFormatter(logging.Formatter):
    """Log timestamps in ICT (UTC+7) regardless of server timezone."""
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, tz=_ICT)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("scanner")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_ICTFormatter(
            "%(asctime)s ICT [%(levelname)s] %(message)s",
        ))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


logger = setup_logging()


# Vietnamese public holidays (fixed dates, update each year)
VN_HOLIDAYS_2025 = {
    date(2025, 1, 1),   # Tết Dương lịch
    date(2025, 1, 27),  # Tết Nguyên Đán (nghỉ bù)
    date(2025, 1, 28),  # Tết Nguyên Đán
    date(2025, 1, 29),  # Tết Nguyên Đán
    date(2025, 1, 30),  # Tết Nguyên Đán
    date(2025, 1, 31),  # Tết Nguyên Đán
    date(2025, 4, 7),   # Giỗ Tổ Hùng Vương
    date(2025, 4, 30),  # Giải phóng miền Nam
    date(2025, 5, 1),   # Quốc tế Lao động
    date(2025, 9, 2),   # Quốc khánh
    date(2025, 9, 3),   # Quốc khánh (nghỉ bù)
}

VN_HOLIDAYS_2026 = {
    date(2026, 1, 1),
    date(2026, 2, 16),
    date(2026, 2, 17),
    date(2026, 2, 18),
    date(2026, 2, 19),
    date(2026, 2, 20),
    date(2026, 4, 27),
    date(2026, 4, 30),
    date(2026, 5, 1),
    date(2026, 9, 2),
}

VN_HOLIDAYS = VN_HOLIDAYS_2025 | VN_HOLIDAYS_2026


def is_trading_day(d: date | None = None) -> bool:
    if d is None:
        d = date.today()
    if d.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return d not in VN_HOLIDAYS


def today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


class StepTimer:
    """
    Context manager hiển thị elapsed time realtime trên cùng 1 dòng.
    Dùng: with StepTimer("Tính indicators"):
    """
    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self._start = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._tick, daemon=True)
        self._thread.start()
        return self

    def _tick(self):
        while not self._stop.is_set():
            elapsed = time.time() - self._start
            sys.stdout.write(f"\r  ⏱  {self.label} ... {elapsed:.1f}s")
            sys.stdout.flush()
            time.sleep(0.1)

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join()
        elapsed = time.time() - self._start
        sys.stdout.write(f"\r  ✓  {self.label} — {elapsed:.1f}s\n")
        sys.stdout.flush()


def fmt_date(d) -> str:
    """Chuyển 'YYYY-MM-DD' hoặc date/Timestamp sang 'DD/MM/YYYY'. Trả '' nếu rỗng."""
    s = str(d or "")[:10]
    if len(s) == 10 and s[4] == "-":
        return f"{s[8:10]}/{s[5:7]}/{s[:4]}"
    return s


def fmt_price(price: float) -> str:
    """Format VND price: 25500 → '25,500', 255.5 → '255.50'"""
    if price >= 1000:
        return f"{price:,.0f}"
    return f"{price:.2f}"


def tv_link(ticker: str, exchange: str = "") -> str:
    """HTML <a> link mở TradingView chart. exchange: HOSE / HNX / UPCOM."""
    exch = exchange.upper() if exchange else "HOSE"
    url = f"https://www.tradingview.com/chart/?symbol={exch}:{ticker}"
    return f'<a href="{url}">{ticker}</a>'


def tk_label(tk_ty: float, short: bool = False) -> str:
    """Nhãn mức thanh khoản theo tỷ VND (TB20).
    short=True: RT/T/TB/C  |  short=False: Rất thấp/Thấp/Trung bình/Cao
    """
    if tk_ty >= 100: return "💎C"  if short else "💎 Cao"
    if tk_ty >= 20:  return "TB"  if short else "Trung bình"
    if tk_ty >= 5:   return "T"   if short else "Thấp"
    return "RT" if short else "Rất thấp"


def fmt_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.1f}%"


def bias_label(bias_norm: float) -> str:
    if bias_norm >= 70:
        return "RẤT MẠNH"
    if bias_norm >= 55:
        return "MẠNH"
    if bias_norm >= 45:
        return "TRUNG TÍNH"
    if bias_norm >= 30:
        return "YẾU"
    return "RẤT YẾU"
