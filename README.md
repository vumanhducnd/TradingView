# ManhDucCapital — VN-Index Stock Scanner

Hệ thống tự động quét tín hiệu kỹ thuật cho top 100 cổ phiếu VN-Index, gửi cảnh báo qua Telegram và xuất báo cáo Excel cuối phiên. Chạy hoàn toàn trên GitHub Actions, miễn phí.

---

## Tổng quan hệ thống

```
Pine Script (ManhDucCapital.pine)   ← logic gốc, chạy trên TradingView
        ↓ (nhân bản lại bằng Python)
scanner/indicators.py               ← tính lại toàn bộ chỉ báo từ OHLCV
scanner/scanner.py                  ← tính SuperTrend, BiasNorm, tín hiệu MUA/BÁN
        ↓
PostgreSQL (Neon serverless)        ← lưu OHLCV + kết quả scan + lịch sử tín hiệu
        ↓
scanner/telegram_bot.py             ← gửi báo cáo cuối phiên
scanner/excel_report.py             ← xuất file .xlsx
scanner/ai_analyst.py               ← phân tích AI từng tín hiệu (Groq/LLaMA)
```

---

## Cấu trúc thư mục

```
TradingView/
├── ManhDucCapital.pine          # Indicator gốc trên TradingView (Pine Script v5)
├── scanner/
│   ├── config.py                # Tham số hệ thống, API keys từ .env
│   ├── database.py              # CRUD PostgreSQL (ohlcv, scan_results, signals)
│   ├── indicators.py            # Tính EMA, VWAP, RSI, MACD, ADX, OBV, Stoch, SuperTrend
│   ├── scanner.py               # Logic chính: BiasNorm, tín hiệu, backtest nhẹ
│   ├── updater.py               # Fetch OHLCV mới → DB → chạy scan → gửi báo cáo
│   ├── live_scanner.py          # Scan liên tục trong phiên (3 phút/lần)
│   ├── telegram_bot.py          # Gửi Telegram: báo cáo cuối phiên, tín hiệu real-time
│   ├── excel_report.py          # Xuất Excel: 6 tab (tín hiệu, vùng xanh, vùng đỏ, lịch sử...)
│   ├── ai_analyst.py            # Phân tích AI từng mã qua Groq API (LLaMA 3.3 70B)
│   ├── crawler.py               # Khởi tạo watchlist từ VN-Index
│   ├── data_fetcher.py          # Fetch OHLCV từ vnstock
│   └── watchlist_builder.py     # Cập nhật danh sách theo dõi
├── .github/workflows/
│   ├── daily_scan.yml           # Scan cuối phiên (15:00 GMT+7, trigger thủ công)
│   ├── live_in_session.yml      # Scan trong phiên (09:00–14:45, 3 phút/lần)
│   ├── live_pre_session.yml     # Nhận định trước phiên (08:45 GMT+7)
│   ├── update_ohlcv.yml         # Cập nhật dữ liệu OHLCV lịch sử
│   └── update_watchlist.yml     # Cập nhật danh sách VN100
├── requirements.txt
└── .env                         # Biến môi trường (không commit)
```

---

## Dual Mode (Dài hạn / Ngắn hạn)

Hệ thống chạy **2 bộ tham số SuperTrend song song**:

| Mode | ATR Period | Multiplier | Bot Telegram |
|---|---|---|---|
| Dài hạn | 10 | 3.0 | `TELEGRAM_BOT_TOKEN_LONG` |
| Ngắn hạn | 7 | 2.0 | `TELEGRAM_BOT_TOKEN_SHORT` |

Mỗi mode gửi báo cáo riêng, xuất file Excel riêng.

---

## BiasNorm — Điểm sức mạnh

9 chỉ báo kỹ thuật, mỗi cái 1 điểm:

| Chỉ báo | Điều kiện bull |
|---|---|
| EMA | EMA9 > EMA21 |
| VWAP | Giá > VWAP |
| RSI | RSI > 52 |
| MACD | MACD cắt lên signal |
| ADX | ADX > 20 |
| OBV | OBV EMA tăng |
| Stochastic | %K cắt lên %D |
| Nến | Close > Open |
| Volume | Volume > MA20 |

`BiasNorm = bull_score / 9 × 100` → `[0, 100]`
- ≥ 55: Bullish
- ≤ 45: Bearish
- 45–55: Trung tính

---

## Tín hiệu MUA / BÁN

**MUA (Breakout):** SuperTrend lật từ -1 → +1 (giá cắt lên trên đường ST)

**BÁN (Reversal):** SuperTrend lật từ +1 → -1 (giá cắt xuống dưới đường ST)

Tín hiệu xác nhận sau **đóng cửa** thanh nến ngày.

---

## Báo cáo Excel (6 tab)

| Tab | Nội dung |
|---|---|
| Tín hiệu trong ngày | Các mã có tín hiệu MUA/BÁN hôm nay |
| Vùng xanh (Nắm giữ) | Mã đang trong uptrend, sort theo TK |
| Vùng đỏ (Đứng ngoài) | Mã đang trong downtrend, sort theo TK |
| Siêu cổ phiếu | Score 5–7/7 tiêu chí dài hạn, TK ≥ 50 tỷ |
| Phân tích AI | Nhận định AI từng tín hiệu (LLaMA 3.3 70B) |
| Lịch sử lệnh | Cặp MUA→BÁN gần nhất, giá + lời/lỗ, sort TK |

---

## Báo cáo Telegram

### Cuối phiên (15:15 GMT+7)
- Header + AI nhận định cuối phiên (nhân cách thay đổi theo thứ trong tuần)
- Top 5 vùng xanh theo thanh khoản
- Tín hiệu bứt phá: Giá mua, Giá SL
- Tín hiệu đảo chiều: Ngày mua, Giá mua, Giá bán, Lời/Lỗ %

### Trong phiên (mỗi 3 phút)
- Cảnh báo tức thì khi xuất hiện tín hiệu mới
- Kèm BiasNorm, hỗ trợ/kháng cự, vị thế đang mở

### Trước phiên (08:45 GMT+7)
- Nhận định thị trường, mã gần điểm mua/bán

---

## Cài đặt và chạy local

### Yêu cầu
- Python 3.12+
- PostgreSQL (hoặc dùng Neon serverless miễn phí)
- vnstock API key (miễn phí tại vnstocks.com)
- Groq API key (miễn phí tại console.groq.com)

### Bước 1: Cài dependencies
```bash
pip install -r requirements.txt
```

### Bước 2: Tạo file `.env`
```env
DATABASE_URL=postgresql://user:pass@host/dbname
VNSTOCK_API_KEY=your_key
GROQ_API_KEY=your_key
TELEGRAM_BOT_TOKEN_LONG=your_token
TELEGRAM_CHAT_ID_LONG=your_chat_id
TELEGRAM_BOT_TOKEN_SHORT=your_token   # có thể dùng chung với LONG
TELEGRAM_CHAT_ID_SHORT=your_chat_id
```

### Bước 3: Khởi tạo DB và watchlist
```bash
python -m scanner.crawler --init-watchlist   # lấy top 100 VN-Index
python -m scanner.updater --fetch-only       # fetch OHLCV lịch sử (~400 ngày)
```

### Bước 4: Chạy scan
```bash
# Scan 1 lần (cuối phiên)
python -m scanner.updater --scan-only --force

# Scan liên tục trong phiên sáng
python -m scanner.live_scanner --mode session --session morning

# Scan liên tục trong phiên chiều
python -m scanner.live_scanner --mode session --session afternoon
```

---

## GitHub Actions (tự động hóa)

Cần thêm các secrets sau vào repository Settings → Secrets:

| Secret | Mô tả |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `VNSTOCK_API_KEY` | API key vnstock |
| `GROQ_API_KEY` | API key Groq (AI) |
| `TELEGRAM_BOT_TOKEN_LONG` | Bot token dài hạn |
| `TELEGRAM_CHAT_ID_LONG` | Chat ID dài hạn (có thể nhiều ID cách nhau dấu phẩy) |
| `TELEGRAM_BOT_TOKEN_SHORT` | Bot token ngắn hạn |
| `TELEGRAM_CHAT_ID_SHORT` | Chat ID ngắn hạn |

Workflows được trigger qua `workflow_dispatch` (thủ công hoặc từ cron-job.org).

---

## Pine Script gốc

`ManhDucCapital.pine` là indicator TradingView độc lập — paste vào Pine Editor để dùng trực tiếp trên chart. Không cần build, không cần server. Logic Python trong `scanner/` là bản nhân bản lại để chạy tự động trên toàn bộ danh sách.
