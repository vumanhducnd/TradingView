"""vnstock thế hệ 6 — áp dụng nguồn dữ liệu khai báo trong scanner/config.py.

Gọi ensure_sources() trước mọi lệnh gọi vnstock (các helper bên dưới đã tự gọi).
"""
import threading

import pandas as pd

from scanner.config import VNSTOCK_SOURCE, VNSTOCK_SOURCE_ROUTES
from scanner.utils import logger

_lock = threading.Lock()
_done = False


def ensure_sources() -> None:
    """Áp dụng quy tắc nguồn một lần cho cả tiến trình (thread-safe)."""
    global _done
    if _done:
        return
    with _lock:
        if _done:
            return
        import vnstock

        if not VNSTOCK_SOURCE and not any(VNSTOCK_SOURCE_ROUTES.values()):
            msg = ("Chưa khai báo nguồn dữ liệu vnstock: đặt biến môi trường VNSTOCK_SOURCE "
                   "(và VNSTOCK_SOURCE_<ROUTE> nếu cần) — xem scanner/config.py")
            logger.error(msg)
            raise RuntimeError(msg)
        try:
            if VNSTOCK_SOURCE:
                vnstock.sources.use(VNSTOCK_SOURCE)
            for route, source in VNSTOCK_SOURCE_ROUTES.items():
                if source:
                    vnstock.sources.use(source, route=route)
        except Exception as e:
            logger.error(f"Cấu hình nguồn vnstock không hợp lệ: {e}")
            raise
        _done = True


def fetch_daily_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Nến ngày một mã (thay cho Quote(symbol, source).history(start, end, "1D"))."""
    ensure_sources()
    from vnstock import Market
    return Market().equity(ticker).ohlcv(start=start, end=end, interval="1D")


def fetch_quote_board(tickers: list[str]) -> pd.DataFrame:
    """Bảng giá nhiều mã (thay cho Trading(source).price_board(symbols_list=...))."""
    ensure_sources()
    from vnstock import Market
    return Market().equity.quote(list(tickers))


def fetch_index_board(symbols: list[str]) -> pd.DataFrame:
    """Chỉ số trong phiên, mỗi chỉ số một dòng mới nhất (thay cho price_board với mã chỉ số)."""
    ensure_sources()
    from vnstock import Market
    frames = []
    for sym in symbols:
        try:
            df = Market().index(sym).quote()
            if df is not None and not df.empty:
                frames.append(df.tail(1))
        except Exception as e:
            logger.debug(f"index quote {sym}: {e}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def is_rate_limit(e: Exception) -> bool:
    """Lỗi giới hạn tần suất (lỗi có kiểu của thế hệ 6, hoặc thông điệp cũ)."""
    try:
        from vnstock.errors import RateLimitError
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass
    err = str(e).lower()
    return "rate limit" in err or "429" in err or "too many" in err
