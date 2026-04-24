# HƯỚNG DẪN SỬ DỤNG CÔNG CỤ MANHDUKCAPITAL

**Hỗ trợ:** Vũ Mạnh Đức — 0973 124 824

---

## MỤC LỤC

1. [Giới thiệu](#1-giới-thiệu)
2. [Cách cài đặt lên TradingView](#2-cách-cài-đặt-lên-tradingview)
3. [Tổng quan giao diện](#3-tổng-quan-giao-diện)
4. [Bảng tín hiệu góc phải (Signal Table)](#4-bảng-tín-hiệu-góc-phải)
5. [Tín hiệu Mua / Bán trên biểu đồ](#5-tín-hiệu-mua--bán-trên-biểu-đồ)
6. [Đường xu hướng SuperTrend](#6-đường-xu-hướng-supertrend)
7. [Nhận diện vị nến (Candle Pattern)](#7-nhận-diện-vị-nến)
8. [Hộp Phân tích trực tiếp](#8-hộp-phân-tích-trực-tiếp)
9. [Bollinger Band (tùy chọn)](#9-bollinger-band)
10. [Backtest lịch sử giao dịch](#10-backtest-lịch-sử-giao-dịch)
11. [Cài đặt chi tiết từng nhóm](#11-cài-đặt-chi-tiết-từng-nhóm)
12. [Cài đặt cảnh báo (Alert)](#12-cài-đặt-cảnh-báo-alert)
13. [Câu hỏi thường gặp](#13-câu-hỏi-thường-gặp)
14. [Lưu ý quan trọng](#14-lưu-ý-quan-trọng)

---

## 1. GIỚI THIỆU

**ManhDucCapital** là công cụ phân tích kỹ thuật chạy trực tiếp trên nền tảng TradingView, được thiết kế đặc biệt cho thị trường **chứng khoán Việt Nam** (hỗ trợ cả USD).

**Công cụ tích hợp sẵn các chỉ báo:**

| Chỉ báo | Vai trò |
|---|---|
| SuperTrend (ATR) | Xác định xu hướng chính, phát tín hiệu Mua/Bán |
| EMA 9 / EMA 21 | Xu hướng ngắn hạn |
| VWAP | Giá trị hợp lý trong ngày |
| RSI 14 | Đo sức mạnh xu hướng |
| MACD (12/26/9) | Đà tăng/giảm |
| ADX 14 | Cường độ xu hướng |
| OBV | Dòng tiền vào/ra |
| Stochastic 14 | Mua/bán quá mức |
| Volume | Xác nhận tín hiệu |

**9 chỉ báo** trên tổng hợp thành điểm **Sức mạnh (0–100%)** hiển thị trực tiếp trên bảng.

---

## 2. CÁCH CÀI ĐẶT LÊN TRADINGVIEW

### Bước 1 — Mở Pine Editor
- Vào TradingView → chọn biểu đồ bất kỳ
- Ở dưới cùng màn hình, bấm tab **"Pine Editor"**

### Bước 2 — Dán code
- Xóa toàn bộ nội dung mặc định trong Pine Editor
- Sao chép toàn bộ nội dung file `ManhDucCapital.pine`
- Dán vào Pine Editor

### Bước 3 — Lưu và thêm vào biểu đồ
- Bấm **"Save"** (Ctrl + S)
- Bấm **"Add to chart"** (Thêm vào biểu đồ)

> Công cụ sẽ xuất hiện ngay trên biểu đồ, hiển thị đường xu hướng và bảng tín hiệu góc phải.

---

## 3. TỔNG QUAN GIAO DIỆN

Sau khi thêm vào biểu đồ, bạn sẽ thấy:

```
┌─────────────────────────────────────────────────────┐
│  Biểu đồ nến                      [Bảng tín hiệu]  │
│                                                     │
│  ── Đường SuperTrend (Xanh/Đỏ) ──                  │
│  ▲ Nhãn MUA (tam giác xanh bên dưới)               │
│  ▼ Nhãn BÁN (tam giác đỏ bên trên)                 │
│  ✪ Vị trí hiện tại trên SuperTrend                 │
│  [Hộp Phân tích trực tiếp — nếu bật]               │
└─────────────────────────────────────────────────────┘
```

---

## 4. BẢNG TÍN HIỆU GÓC PHẢI

Bảng nhỏ **luôn hiển thị** ở góc trên bên phải màn hình (khi bật "Hiển thị bảng tín hiệu").

### Ý nghĩa từng dòng:

| Dòng | Nội dung | Ý nghĩa |
|---|---|---|
| **Tên mã** | VD: `HPG`, `VNM` | Mã chứng khoán đang xem |
| **Trạng thái xu hướng** | `▲ MUA VÀ NẮM GIỮ` hoặc `▼ BÁN VÀ CHỜ ĐỢI` | Xu hướng hiện tại |
| **NGÀY MUA/BÁN** | DD/MM/YYYY | Ngày phát tín hiệu gần nhất |
| **GIÁ MUA/BÁN** | Giá tại thời điểm tín hiệu | Điểm vào lệnh tham khảo |
| **HỖ TRỢ / KHÁNG CỰ** | Giá theo màu Xanh/Đỏ | Mức giá SuperTrend hiện tại |
| **GIỮ LỆNH** | X PHIÊN | Số phiên đã trôi qua kể từ tín hiệu |
| **ĐANG LÃI / ĐANG LỖ** | +X% hoặc -X% | % thay đổi giá so với điểm vào lệnh |

### Màu sắc trạng thái:
- **Xanh lá** → Xu hướng tăng, đang có lãi
- **Đỏ** → Xu hướng giảm, hoặc đang lỗ

---

## 5. TÍN HIỆU MUA / BÁN TRÊN BIỂU ĐỒ

### Tín hiệu Mua (▲ Xanh)
- Xuất hiện **bên dưới** nến, hình tam giác **xanh lá** chỉ lên
- Nhãn hiển thị: **"Mua"** kèm giá tham chiếu
- Ý nghĩa: Xu hướng vừa đảo chiều từ **Giảm → Tăng**

### Tín hiệu Bán (▼ Đỏ)
- Xuất hiện **bên trên** nến, hình tam giác **đỏ** chỉ xuống
- Nhãn hiển thị: **"Bán"** kèm giá tham chiếu
- Ý nghĩa: Xu hướng vừa đảo chiều từ **Tăng → Giảm**

### Nguyên tắc sử dụng:
- **Nhận tín hiệu MUA** → Xem xét mở vị thế, đặt stop-loss dưới đường SuperTrend xanh
- **Nhận tín hiệu BÁN** → Xem xét chốt lời / tránh mua thêm
- Tín hiệu **càng mạnh** (đi kèm Volume cao, Sức mạnh ≥ 60%) → **Độ tin cậy càng cao**

---

## 6. ĐƯỜNG XU HƯỚNG SUPERTREND

Đường kẻ chạy theo biểu đồ là **đường SuperTrend**:

| Màu | Ý nghĩa | Hành động gợi ý |
|---|---|---|
| **Xanh lá** (dưới nến) | Xu hướng **TĂNG** | Nắm giữ / xem xét mua thêm khi giá pullback về đường xanh |
| **Đỏ** (trên nến) | Xu hướng **GIẢM** | Đứng ngoài / chờ đợi |

**Vùng tô màu** giữa đường SuperTrend và giá nến:
- Vùng **xanh nhạt** → Đệm an toàn khi xu hướng tăng
- Vùng **đỏ nhạt** → Áp lực bán khi xu hướng giảm

**Biểu tượng ✪** (dấu sao) — đánh dấu vị trí SuperTrend ở nến hiện tại.

---

## 7. NHẬN DIỆN VỊ NẾN

Khi giá **tiếp cận gần** đường Hỗ trợ/Kháng cự (trong ngưỡng % bạn cài đặt), công cụ tự động nhận diện và hiển thị nhãn vị nến:

### Vị nến tăng (gần vùng Hỗ trợ — xu hướng Tăng):

| Biểu tượng | Tên | Ý nghĩa |
|---|---|---|
| 🔨 | **Hammer** | Đuôi dài dưới — khả năng bật lại từ hỗ trợ |
| 🕯️ | **Bullish Engulfing** | Nến tăng bao phủ nến giảm — đảo chiều tăng |
| 🌅 | **Dragonfly Doji** | Doji đuôi dài dưới — chú ý đảo chiều tăng |
| 〰️ | **Doji** | Giằng co tại hỗ trợ — chờ xác nhận |
| 💚 | **Marubozu Tăng** | Nến tăng thân dài — tín hiệu tích cực |

### Vị nến giảm (gần vùng Kháng cự — xu hướng Giảm):

| Biểu tượng | Tên | Ý nghĩa |
|---|---|---|
| ⭐ | **Shooting Star** | Đuôi dài trên — cảnh báo đảo chiều tại kháng cự |
| 🕯️ | **Bearish Engulfing** | Nến giảm bao phủ nến tăng — xác nhận áp lực bán |
| 🌃 | **Gravestone Doji** | Doji đuôi dài trên — cảnh báo đảo chiều giảm |
| 〰️ | **Doji** | Giằng co tại kháng cự — chờ xác nhận |
| 🔴 | **Marubozu Giảm** | Nến giảm thân dài — áp lực bán lớn |

> **Lưu ý:** Vị nến chỉ có giá trị khi giá **nằm gần** vùng hỗ trợ/kháng cự. Cần kết hợp với tín hiệu tổng thể trước khi ra quyết định.

---

## 8. HỘP PHÂN TÍCH TRỰC TIẾP

> Bật bằng cách tích chọn **"Hiển thị Phân tích trực tiếp?"** trong Cài đặt chung.

Hộp phân tích xuất hiện bên cạnh nến cuối cùng, hiển thị toàn bộ trạng thái thị trường **theo thời gian thực**:

### Nội dung hộp phân tích:

**Dòng chỉ báo kỹ thuật:**
```
RSI:XX↑  MACD:↑  ADX:XX💪  OBV:↑
EMA:9>21↑  VWAP:↑
```
- `↑` → Chỉ báo đang ủng hộ xu hướng tăng
- `↓` → Chỉ báo đang ủng hộ xu hướng giảm
- `→` → Chỉ báo trung tính
- `💪` → ADX > 20, xu hướng đủ mạnh | `😴` → ADX ≤ 20, xu hướng yếu

**Dòng Volume:**
```
Mua: XXXX | Bán: XXXX | Delta: +XX%
Vol/TB20: 🔥 2.5x — Đột biến!
```
- Volume mua/bán tích lũy từ khi xu hướng đổi chiều
- Delta dương (+) → Tiền vào nhiều hơn tiền ra
- 🔥 Đột biến (≥2x) → Tín hiệu mạnh hơn

**Sức mạnh xu hướng:**

| Mức | Màu | Ý nghĩa |
|---|---|---|
| ≥ 75% | Tím | RẤT MẠNH |
| 55–74% | Xanh lá | MẠNH |
| 45–54% | Vàng | TRUNG BÌNH |
| 25–44% | Cam | YẾU |
| < 25% | Đỏ | RẤT YẾU |

**Mục tiêu chốt lời (khi đang giữ lệnh MUA):**

| Mục tiêu | % Lãi | Gợi ý hành động |
|---|---|---|
| T1 | +20% | Chốt 30% vị thế, giữ phần còn lại |
| T2 | +40% | Chốt thêm 40%, gồng phần cuối |
| T3 | +80% | Cân nhắc chốt toàn bộ hoặc đặt trailing stop |

**Khuyến nghị hành động (Action):**

| Biểu tượng | Tình huống |
|---|---|
| 🚀 | Tín hiệu bứt phá — thời điểm mở vị thế tốt |
| 📈 | Dòng tiền vào tốt — tiếp tục nắm giữ |
| 👀 | Momentum chậm lại — giữ nhưng quan sát kỹ |
| ⚠️ | Tín hiệu chưa đồng thuận — chưa nên tăng tỷ trọng |
| ⛔ | Áp lực bán chiếm ưu thế — đứng ngoài quan sát |
| 🔍 | Có dấu hiệu hồi kỹ thuật — chờ xác nhận |
| ⚖️ | Thị trường tích lũy — chờ breakout |

---

## 9. BOLLINGER BAND

> Bật bằng cách tích chọn **"Hiển thị Bollinger Band"** trong nhóm Bollinger Band.

**Cách đọc Bollinger Band:**

| Tình huống | Ý nghĩa |
|---|---|
| Giá chạm band **trên** | Xem xét chốt lời / cẩn thận mua thêm |
| Giá chạm band **dưới** | Vùng có thể mua vào (nếu xu hướng tăng) |
| BB **co hẹp lại** | Sắp có biến động mạnh (breakout) |
| BB **mở rộng** | Xu hướng đang mạnh |

Màu Bollinger Band theo xu hướng:
- **Xanh** → Xu hướng tăng
- **Đỏ** → Xu hướng giảm

---

## 10. BACKTEST LỊCH SỬ GIAO DỊCH

> Bật bằng cách tích chọn **"Hiển thị bảng lịch sử giao dịch?"** trong nhóm Test lịch sử.

Tính năng này **mô phỏng** kết quả nếu bạn theo tín hiệu Mua/Bán trong khoảng thời gian đã chọn.

### Cách thiết lập Backtest:

1. Tích chọn **"Hiển thị bảng lịch sử giao dịch?"**
2. Chọn **"Từ ngày"** — ngày bắt đầu backtest
3. Nếu muốn giới hạn khoảng thời gian: tích **"Dùng ngày kết thúc?"** và chọn ngày kết thúc
4. Nhập **"Vốn ban đầu (triệu VNĐ)"** — ví dụ: 100 = 100 triệu VNĐ
5. Chọn **kiểu lãi kép**:
   - **BẬT** (Xoay vốn ♻️) → Dùng toàn bộ vốn hiện tại mỗi lệnh, lãi kép
   - **TẮT** (Vốn cố định 📦) → Luôn dùng đúng vốn ban đầu

### Kiểu điểm Mua/Bán (Backtest):

| Chế độ | Mua tại | Bán tại | Ý nghĩa |
|---|---|---|---|
| 🏆 Lý tưởng nhất | Giá Thấp nhất | Giá Cao nhất | Kịch bản tốt nhất có thể |
| ⭐ Lý tưởng | Giá HL2 (TB) | Giá HL2 (TB) | Kịch bản trung bình |
| 📊 Thực tế | Giá Cao nhất | Giá Thấp nhất | Kịch bản thực tế nhất |

> **Khuyến nghị:** Dùng chế độ **"Thực tế"** để có cái nhìn chính xác nhất.

### Đọc kết quả Backtest:

Bảng hiển thị từng giao dịch gồm: Ngày Mua, Giá Mua, Ngày Bán, Giá Bán, Số phiên giữ, Lãi/Lỗ (triệu), % Lãi/Lỗ.

**Dòng tổng kết:**
- **Vốn đầu → Vốn cuối** — tổng vốn sau toàn bộ giao dịch
- **ROI%** — % lợi nhuận tổng
- **Win Rate%** — tỷ lệ lệnh thắng
- **Avg PnL%** — lãi/lỗ trung bình mỗi lệnh
- **Max DD%** — mức thua lỗ tối đa từ đỉnh (Drawdown)

> ⚠️ **Kết quả quá khứ không đảm bảo hiệu suất tương lai.**

---

## 11. CÀI ĐẶT CHI TIẾT TỪNG NHÓM

Vào biểu đồ → Bấm **⚙️ (bánh răng)** trên tên công cụ để mở cài đặt.

### Nhóm: Cài đặt chung

| Tùy chọn | Mặc định | Ghi chú |
|---|---|---|
| Hiển thị bảng tín hiệu? | Bật | Bảng góc phải màn hình |
| Cỡ chữ bảng | normal | Chọn: tiny / small / normal / large / huge |
| Giao diện (Theme) | Light | Chọn: Dark hoặc Light |
| Hiển thị tín hiệu Mua/Bán? | Bật | Nhãn MUA/BÁN trên biểu đồ |
| Hiển thị đường Hỗ trợ/Kháng cự? | Tắt | Đường kẻ đứt nét kèm nhãn HT/KC |
| Hiển thị Phân tích trực tiếp? | Tắt | Hộp phân tích chi tiết |
| Đọc vị nến khi gần đỉnh/đáy? | Bật | Nhận diện mẫu nến đảo chiều |
| Ngưỡng gần đỉnh/đáy (%) | 3.0% | Khoảng cách % để coi là "gần" vùng HT/KC |
| Số nhãn vị nến tối đa | 3 | Số nhãn hiển thị trên biểu đồ |

### Nhóm: Tín hiệu (SuperTrend)

| Tùy chọn | Mặc định | Ghi chú |
|---|---|---|
| Chu kỳ ATR | 10 | Tăng → tín hiệu ít hơn, ít nhiễu hơn |
| Hệ số nhân ATR | 3.0 | Tăng → đường SuperTrend xa nến hơn |
| Thay đổi phương pháp tính ATR? | Bật | Bật = dùng ATR chuẩn; Tắt = dùng SMA của TR |
| Lọc sức mạnh khi đảo chiều? | Tắt | Bật = chỉ đảo chiều khi điểm Sức mạnh đủ ngưỡng |
| Ngưỡng YẾU (≤) cho Bán | 40 | Chỉ đảo chiều Bán khi Sức mạnh ≤ 40% |
| Ngưỡng MẠNH (≥) cho Mua | 60 | Chỉ đảo chiều Mua khi Sức mạnh ≥ 60% |

> **Mẹo:** Bật **"Lọc sức mạnh"** để giảm tín hiệu giả, nhưng có thể bỏ lỡ một số cơ hội.

### Nhóm: Bollinger Band

| Tùy chọn | Mặc định | Ghi chú |
|---|---|---|
| Hiển thị Bollinger Band | Tắt | Bật để hiện BB trên biểu đồ |
| Chu kỳ BB | 20 | Số nến tính BB |
| Độ lệch chuẩn BB | 2.0 | Tăng = BB rộng hơn |

---

## 12. CÀI ĐẶT CẢNH BÁO (ALERT)

Công cụ có sẵn **3 loại cảnh báo** để bạn nhận thông báo ngay khi có tín hiệu:

| Tên cảnh báo | Kích hoạt khi |
|---|---|
| Cảnh báo điểm Mua | Xuất hiện tín hiệu MUA mới |
| Cảnh báo điểm Bán | Xuất hiện tín hiệu BÁN mới |
| Cảnh báo điểm Đảo chiều | Xu hướng đổi chiều (Tăng ↔ Giảm) |

### Cách tạo Alert:
1. Bấm biểu tượng **🔔 (chuông)** trên TradingView → **"Create Alert"**
2. Phần **Condition** → chọn **"ManhDucCapital"**
3. Chọn điều kiện: `Cảnh báo điểm Mua` / `Cảnh báo điểm Bán` / `Cảnh báo điểm Đảo chiều`
4. Cài thông báo qua Email, App, Webhook tùy nhu cầu
5. Bấm **"Create"**

---

## 13. CÂU HỎI THƯỜNG GẶP

**Q: Tín hiệu MUA xuất hiện rồi nhưng giá vẫn giảm tiếp, phải làm gì?**
> Không có công cụ nào chính xác 100%. Nếu giá phá xuống dưới đường SuperTrend xanh, xu hướng có thể đảo chiều → chờ tín hiệu mới.

**Q: Nên dùng khung thời gian nào?**
> Khung **ngày (1D)** phù hợp nhất cho chứng khoán cơ sở Việt Nam. Khung tuần (1W) cho giao dịch trung/dài hạn. Không nên dùng khung quá nhỏ (< 15 phút) vì nhiều nhiễu.

**Q: Sức mạnh bao nhiêu thì nên mua?**
> Sức mạnh **≥ 60% (màu xanh)** kết hợp tín hiệu MUA là tốt nhất. Sức mạnh ≥ 75% (màu tím) là cực kỳ mạnh.

**Q: Tại sao bảng Backtest không hiện lên?**
> Cần đảm bảo: (1) đã bật "Hiển thị bảng lịch sử giao dịch?", (2) khoảng thời gian Backtest có ít nhất 1 lệnh đã đóng (có cả tín hiệu MUA và BÁN).

**Q: Giá hiển thị trên nhãn bị chia 1000 có đúng không?**
> Đúng. Công cụ tự động chuyển đổi giá VNĐ: ví dụ `25500 VNĐ → hiển thị 25.5`. Đây là định dạng chuẩn của thị trường chứng khoán Việt Nam.

**Q: Có thể dùng cho cổ phiếu nước ngoài (USD) không?**
> Được. Giá < 10,000 sẽ hiển thị dạng thập phân chuẩn USD (ví dụ: 125.50).

---

## 14. LƯU Ý QUAN TRỌNG

> ⚠️ **TUYÊN BỐ MIỄN TRỪ TRÁCH NHIỆM**

- Công cụ này **chỉ mang tính tham khảo**, không phải khuyến nghị mua bán chứng khoán
- **Không đảm bảo lợi nhuận** trong tương lai
- Kết quả backtest dựa trên dữ liệu **lịch sử** — thị trường tương lai có thể khác hoàn toàn
- **Người dùng tự chịu trách nhiệm** với mọi quyết định đầu tư
- Luôn kết hợp với phân tích cơ bản và quản lý rủi ro cá nhân

---

**Hỗ trợ kỹ thuật:**
Vũ Mạnh Đức — 📞 **0973 124 824**
