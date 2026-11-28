"""Lớp lấy dữ liệu thị trường DUY NHẤT của dự án — mọi lệnh gọi dữ liệu đi qua đây.

DATA_PROVIDER (scanner/config.py) chọn nhà cung cấp:
  - "legacy"  : gọi thẳng API nội bộ VCI/KBS như vnstock 4.x (scanner/legacy_sources.py)
  - "vnstock" : vnstock thế hệ 6, nguồn theo VNSTOCK_SOURCE / VNSTOCK_SOURCE_<ROUTE>
Mọi hàm trả về cùng tên cột ở cả hai chế độ.
"""
import threading

import pandas as pd

from scanner.config import DATA_PROVIDER, VNSTOCK_SOURCE, VNSTOCK_SOURCE_ROUTES
from scanner.utils import logger

_lock = threading.Lock()
_done = False


def _legacy() -> bool:
    return DATA_PROVIDER == "legacy"


def ensure_sources() -> None:
    """Chế độ vnstock: áp dụng quy tắc nguồn một lần cho cả tiến trình. Chế độ legacy: không làm gì."""
    global _done
    if _done or _legacy():
        return
    with _lock:
        if _done:
            return
        if DATA_PROVIDER != "vnstock":
            msg = f"DATA_PROVIDER không hợp lệ: {DATA_PROVIDER!r} (chọn 'legacy' hoặc 'vnstock')"
            logger.error(msg)
            raise RuntimeError(msg)
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
    """Nến ngày một mã: time, open, high, low, close (cổ phiếu: nghìn đồng; chỉ số: điểm), volume."""
    from scanner import legacy_sources
    if _legacy():
        return legacy_sources.fetch_daily_ohlcv(ticker, start, end)
    ensure_sources()
    from vnstock import Market
    if legacy_sources.is_index(ticker):
        return Market().index(ticker.upper()).ohlcv(start=start, end=end, interval="1D")
    return Market().equity(ticker).ohlcv(start=start, end=end, interval="1D")


def fetch_quote_board(tickers: list[str]) -> pd.DataFrame:
    """Bảng giá nhiều mã: symbol, open_price, high_price, low_price, close_price (đồng), total_volume."""
    if _legacy():
        from scanner import legacy_sources
        return legacy_sources.fetch_quote_board(tickers)
    ensure_sources()
    from vnstock import Market
    return Market().equity.quote(list(tickers))


def fetch_index_board(symbols: list[str]) -> pd.DataFrame:
    """Chỉ số, mỗi chỉ số một dòng: symbol, close_price (điểm), percent_change (%)."""
    if _legacy():
        from scanner import legacy_sources
        return legacy_sources.fetch_index_board(symbols)
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


def fetch_index_members(index: str) -> pd.DataFrame:
    """Thành phần rổ chỉ số (VN100…): cột symbol."""
    if _legacy():
        from scanner import legacy_sources
        return legacy_sources.fetch_index_members(index)
    ensure_sources()
    from vnstock import Reference
    return Reference().index(index).members()


def fetch_exchange_symbols(exchange: str) -> pd.DataFrame:
    """Mã niêm yết theo sàn HOSE / HNX / UPCOM: cột symbol."""
    if _legacy():
        from scanner import legacy_sources
        return legacy_sources.fetch_exchange_symbols(exchange)
    ensure_sources()
    from vnstock import Reference
    return Reference().equity.list(exchange=exchange)


def fetch_industry_sectors() -> pd.DataFrame:
    """Mã kèm ngành: cột symbol + icb_name (vnstock) hoặc industry_name (legacy, phân ngành KBS)."""
    if _legacy():
        from scanner import legacy_sources
        return legacy_sources.fetch_industry_sectors()
    ensure_sources()
    from vnstock import Reference
    return Reference().industry.sectors()


def fetch_foreign_room(tickers: list[str]) -> pd.DataFrame:
    """Room ngoại: symbol, foreign_room (còn lại), foreign_room_total, listed_shares."""
    if _legacy():
        from scanner import legacy_sources
        return legacy_sources.fetch_foreign_room(tickers)
    ensure_sources()
    from vnstock import Market, Reference
    df = Market().equity.foreign_room(list(tickers))
    if df is None or df.empty:
        return pd.DataFrame(columns=["symbol", "foreign_room", "foreign_room_total", "listed_shares"])
    listing = Reference().equity.list()[["symbol", "listed_shares"]]
    return df.merge(listing, on="symbol", how="left")


def is_rate_limit(e: Exception) -> bool:
    """Lỗi giới hạn tần suất (lỗi có kiểu của vnstock 6, HTTP 429, hoặc thông điệp cũ)."""
    try:
        from vnstock.errors import RateLimitError
        if isinstance(e, RateLimitError):
            return True
    except ImportError:
        pass
    err = str(e).lower()
    return "rate limit" in err or "429" in err or "too many" in err
