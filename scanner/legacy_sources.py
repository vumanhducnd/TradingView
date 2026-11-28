"""Nguồn dữ liệu "legacy": gọi thẳng API nội bộ của VCI (Vietcap) và KBS như vnstock 4.x từng làm.

⚠️ Đây KHÔNG phải API chính thức dành cho bên ngoài: không cần tài khoản, nhưng có thể đổi địa chỉ,
thêm xác thực hoặc chặn bất cứ lúc nào, và việc dùng tự động / hiển thị cho người khác tuỳ điều khoản
của VCI, KBS. Bật/tắt bằng DATA_PROVIDER trong scanner/config.py.

Địa chỉ và cách chuẩn hoá lấy từ mã nguồn vnstock 4.0.5 (explorer/vci, explorer/kbs) để kết quả giữ
nguyên như bản cũ: nến ngày giá nghìn đồng (chỉ số giữ nguyên điểm), làm tròn 2 chữ số, giờ Việt Nam;
bảng giá giá đồng; danh mục theo sàn và ngành theo KBS.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
import requests

VCI_TRADING = "https://trading.vietcap.com.vn/api/"
KBS_IIS = "https://kbbuddywts.kbsec.com.vn/iis-server/investment"
TIMEOUT = 15
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
VCI_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/json",
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
    "User-Agent": _UA,
}
KBS_HEADERS = {"Accept": "application/json", "User-Agent": _UA}

# Mã chỉ số theo VCI (vnstock 4.x explorer/vci/const.py: _VCI_INDEX_MAPPING)
VCI_INDEX = {"VNINDEX": "VNINDEX", "VNI": "VNINDEX", "VN30": "VN30", "VN100": "VN100",
             "HNX": "HNXIndex", "HNXINDEX": "HNXIndex", "HNX30": "HNX30",
             "UPCOM": "HNXUpcomIndex", "UPCOMINDEX": "HNXUpcomIndex"}
# Mã nhóm theo KBS (vnstock 4.x explorer/kbs/const.py: _GROUP_CODE)
KBS_GROUP = {"HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM", "VN30": "30", "VN100": "100"}
_BOARD = {"HSX": "HOSE", "HOSE": "HOSE", "HNX": "HNX", "UPCOM": "UPCOM"}
_TZ = "Asia/Ho_Chi_Minh"


def _vci_post(path: str, payload: dict):
    r = requests.post(VCI_TRADING + path, headers=VCI_HEADERS, json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _vci_get(path: str):
    r = requests.get(VCI_TRADING + path, headers=VCI_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def _kbs_get(path: str):
    r = requests.get(KBS_IIS + path, headers=KBS_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    j = r.json()
    return j["data"] if isinstance(j, dict) and "data" in j else j


def _gap_chart(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Nến ngày thô từ VCI: time (tz Asia/Ho_Chi_Minh), open..close (đơn vị gốc), volume."""
    start_dt = datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)
    count_back = len(pd.bdate_range(start=start_dt, end=end_dt)) + 1
    data = _vci_post("chart/OHLCChart/gap-chart", {
        "timeFrame": "ONE_DAY", "symbols": [symbol],
        "to": int(end_dt.timestamp()), "countBack": count_back,
    })
    if isinstance(data, dict) and "data" in data:
        data = data["data"]
    if not data or not isinstance(data, list) or not data[0].get("t"):
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    d = data[0]
    df = pd.DataFrame({"time": d["t"], "open": d["o"], "high": d["h"],
                       "low": d["l"], "close": d["c"], "volume": d["v"]})
    df["time"] = pd.to_datetime(df["time"].astype(int), unit="s", utc=True).dt.tz_convert(_TZ)
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype("float64")
    df["volume"] = df["volume"].astype("int64")
    day = df["time"].dt.tz_localize(None).dt.normalize()
    return df[(day >= pd.Timestamp(start)) & (day <= pd.Timestamp(end))].reset_index(drop=True)


def is_index(symbol: str) -> bool:
    return symbol.upper() in VCI_INDEX


def fetch_daily_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Nến ngày — cổ phiếu: giá nghìn đồng; chỉ số: giữ nguyên điểm (như vnstock 4.x VCI).
    Làm tròn 2 chữ số."""
    sym = ticker.upper()
    index = is_index(sym)
    df = _gap_chart(VCI_INDEX[sym] if index else sym, start, end)
    if not df.empty:
        cols = ["open", "high", "low", "close"]
        df[cols] = (df[cols] if index else df[cols] / 1000).round(2)
    return df


def _price_board_raw(symbols: list[str]) -> list[dict]:
    out: list[dict] = []
    for i in range(0, len(symbols), 500):
        out += _vci_post("price/symbols/getList", {"symbols": list(symbols[i:i + 500])}) or []
    return out


def fetch_quote_board(tickers: list[str]) -> pd.DataFrame:
    """Bảng giá — cột như vnstock thế hệ 6 profile v5, giá đơn vị đồng (như price_board cũ)."""
    rows = []
    for it in _price_board_raw([t.upper() for t in tickers]):
        li, mp = it.get("listingInfo") or {}, it.get("matchPrice") or {}
        rows.append({
            "symbol": li.get("symbol") or mp.get("symbol"),
            "exchange": _BOARD.get(li.get("board"), li.get("board")),
            "reference_price": li.get("refPrice"),
            "open_price": mp.get("openPrice"),
            "high_price": mp.get("highest"),
            "low_price": mp.get("lowest"),
            "close_price": mp.get("matchPrice"),
            "total_volume": mp.get("accumulatedVolume"),
        })
    return pd.DataFrame(rows)


def fetch_index_board(symbols: list[str]) -> pd.DataFrame:
    """Chỉ số: điểm đóng cửa phiên gần nhất và % thay đổi so với phiên trước (từ nến ngày VCI)."""
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    rows = []
    for sym in symbols:
        try:
            df = _gap_chart(VCI_INDEX.get(sym.upper(), sym), start, end)
            if len(df) >= 2:
                last, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
                rows.append({"symbol": sym, "close_price": last,
                             "percent_change": (last / prev - 1) * 100 if prev else 0.0})
        except Exception:
            continue
    return pd.DataFrame(rows)


def fetch_index_members(index: str) -> pd.DataFrame:
    """Thành phần rổ chỉ số (VCI getByGroup, như Listing(source='VCI').symbols_by_group)."""
    data = _vci_get(f"price/symbols/getByGroup?group={VCI_INDEX.get(index.upper(), index)}")
    return pd.DataFrame({"symbol": [x["symbol"] for x in data or [] if x.get("symbol")]})


def fetch_exchange_symbols(exchange: str) -> pd.DataFrame:
    """Mã theo sàn HOSE/HNX/UPCOM (KBS, như Listing(source='KBS').symbols_by_group(sàn))."""
    data = _kbs_get(f"/index/{KBS_GROUP.get(exchange.upper(), exchange)}/stocks")
    return pd.DataFrame({"symbol": [s for s in data or [] if isinstance(s, str)]})


def fetch_industry_sectors() -> pd.DataFrame:
    """Ngành theo phân loại KBS (như Listing(source='KBS').symbols_by_industries)."""
    rows = []
    for sec in _kbs_get("/sector/all") or []:
        try:
            j = _kbs_get(f"/sector/stock?code={sec['code']}&l=1")
        except Exception:
            continue
        stocks = j.get("stocks", []) if isinstance(j, dict) else []
        rows += [{"symbol": s["sb"], "industry_code": sec["code"], "industry_name": sec["name"]}
                 for s in stocks if s.get("sb")]
    return pd.DataFrame(rows, columns=["symbol", "industry_code", "industry_name"])


def fetch_foreign_room(tickers: list[str]) -> pd.DataFrame:
    """Room ngoại từ bảng giá VCI: foreign_room (còn lại), foreign_room_total, listed_shares."""
    rows = []
    for it in _price_board_raw([t.upper() for t in tickers]):
        li, mp = it.get("listingInfo") or {}, it.get("matchPrice") or {}
        rows.append({"symbol": li.get("symbol") or mp.get("symbol"),
                     "foreign_room": mp.get("currentRoom"),
                     "foreign_room_total": mp.get("totalRoom"),
                     "listed_shares": li.get("listedShare")})
    return pd.DataFrame(rows)
