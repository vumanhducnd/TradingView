# CLAUDE.md

## Dự án gồm 2 phần độc lập

1. **`ManhDucCapital.pine`** — TradingView indicator (Pine Script v5), dùng trực tiếp trên chart, không build
2. **`scanner/`** — Python system nhân bản logic Pine Script, chạy tự động trên GitHub Actions

---

## Python Scanner — Luồng dữ liệu

```
vnstock API → data_fetcher.py → ohlcv (PostgreSQL)
                                     ↓
                              indicators.py → scanner.py → scan_results + signals (DB)
                                                                  ↓
                              telegram_bot.py ← excel_report.py ← ai_analyst.py
```

**Entry point:** `scanner/updater.py` (cuối phiên) và `scanner/live_scanner.py` (trong phiên)

---

## Các file quan trọng

| File | Vai trò |
|---|---|
| `scanner/config.py` | Tham số ST, chỉ báo, API keys từ `.env` |
| `scanner/database.py` | CRUD cho 3 bảng chính: `ohlcv`, `scan_results`, `signals` |
| `scanner/indicators.py` | Tính EMA/VWAP/RSI/MACD/ADX/OBV/Stoch/SuperTrend từ OHLCV |
| `scanner/scanner.py` | BiasNorm (9 điểm), tín hiệu MUA/BÁN, last_signal tracking |
| `scanner/telegram_bot.py` | Gửi báo cáo cuối phiên + tín hiệu real-time |
| `scanner/excel_report.py` | Xuất 6-tab Excel: tín hiệu, vùng xanh/đỏ, AI, lịch sử lệnh |
| `scanner/ai_analyst.py` | Gọi Groq API (LLaMA 3.3 70B) phân tích từng tín hiệu |

---

## Dual Mode

Hệ thống chạy 2 bộ SuperTrend song song, mỗi cái có cột riêng trong DB:

- **long**: ATR=10, mult=3.0 — cột prefix `long_*` — bot `TELEGRAM_BOT_TOKEN_LONG`
- **short**: ATR=7, mult=2.0 — cột prefix `short_*` — bot `TELEGRAM_BOT_TOKEN_SHORT`

Khi `long_buy_signal` tồn tại trong DataFrame → đang ở dual mode. Tất cả code phân nhánh theo flag này.

---

## Database schema (3 bảng chính)

**`ohlcv`**: `ticker, date, open, high, low, close, volume, turnover`

**`scan_results`**: 1 hàng/ticker, upsert mỗi phiên. Cột quan trọng:
- `long_last_signal_type` / `long_last_signal_date` / `long_last_signal_price` — tín hiệu gần nhất
- `long_signal_pnl_pct` — P&L từ tín hiệu đó đến giá hiện tại
- Tương tự với prefix `short_`

**`signals`**: log lịch sử tín hiệu. `ticker, signal_date, signal_type (MUA/BÁN), style (long/short)`
- Không lưu giá — giá tra từ `ohlcv` theo `signal_date`

---

## Quy tắc giá (fmt_price)

Giá chứng khoán VN lưu dạng nguyên (VD: `25500`). Hàm `fmt_price` trong `utils.py`:
- `< 1000`: giữ nguyên (giá USD-style)
- `≥ 1000`: chia 1000 → hiển thị `25.5`

---

## BiasNorm

9 chỉ báo bull/bear → `bull_score/9 × 100`. Ngưỡng: ≥55 bullish, ≤45 bearish. Tên cột trong DB: `bias_norm`, `b_score`, `r_score`.

---

## Conventions

- Tên cột DB dùng snake_case, prefix `long_`/`short_` cho dual mode
- `_val(row, col1, col2)` — helper lấy giá trị đầu tiên không-NaN (fallback giữa dual/single mode)
- `turnover` đơn vị VND thô (×10⁹ = 1 tỷ) — luôn chia `1e9` khi hiển thị
- Mọi render bảng Excel/Telegram gated sau khi có đủ data, không sửa logic tính toán ở đó
