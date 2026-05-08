# HƯỚNG DẪN SỬ DỤNG CHỈ BÁO MANHDUCCAPITAL
## Chiến Lược: Vào Lệnh Ngắn Hạn — Nắm Giữ Dài Hạn
### Tác giả: Vũ Mạnh Đức | 0973124824

---

## TUYÊN BỐ MIỄN TRỪ TRÁCH NHIỆM

Chỉ báo ManhDucCapital chỉ mang tính tham khảo kỹ thuật. Không phải khuyến nghị mua bán chứng khoán. Không đảm bảo lợi nhuận. Người dùng tự chịu trách nhiệm với mọi quyết định đầu tư. Kết quả quá khứ không đảm bảo hiệu suất tương lai.

---

# PHẦN 1: TỔNG QUAN VỀ CHỈ BÁO

## 1.1. ManhDucCapital Là Gì?

ManhDucCapital là một chỉ báo kỹ thuật toàn diện được xây dựng trên nền tảng TradingView Pine Script v6. Chỉ báo này được thiết kế đặc biệt cho thị trường chứng khoán Việt Nam (HOSE, HNX, UPCOM), tích hợp đồng thời nhiều công cụ phân tích kỹ thuật trong một giao diện thống nhất.

Điểm đặc biệt của ManhDucCapital là khả năng kết hợp phân tích xu hướng (SuperTrend), đánh giá sức mạnh thị trường (Dual Score 9 điểm), nhận diện mẫu nến, quản lý rủi ro (TP/SL động), và kiểm thử lịch sử — tất cả trong một công cụ duy nhất.

## 1.2. Triết Lý Giao Dịch Cốt Lõi

Chỉ báo được xây dựng dựa trên triết lý:

**"Vào lệnh sớm bằng tín hiệu ngắn hạn — Nắm giữ kiên nhẫn theo xu hướng dài hạn"**

Cụ thể:
- Dùng chế độ **Ngắn hạn** (ATR=7, Hệ số=2) để bắt tín hiệu MUA sớm, nhạy hơn với biến động giá
- Sau khi vào lệnh, chuyển sang tư duy **Dài hạn** để nắm giữ qua các nhịp điều chỉnh
- Chỉ thoát lệnh khi xuất hiện tín hiệu BÁN rõ ràng từ SuperTrend hoặc sức mạnh suy yếu nghiêm trọng

## 1.3. Cấu Trúc Tổng Thể Của Chỉ Báo

Chỉ báo bao gồm các tầng phân tích chồng lên nhau:

| Tầng | Tên | Chức năng |
|------|-----|-----------|
| 1 | SuperTrend | Xác định xu hướng chính (tăng/giảm) |
| 2 | Dual Score 9 điểm | Đo sức mạnh thực sự của xu hướng |
| 3 | Siêu Cổ Phiếu | Lọc cổ phiếu đẳng cấp cao |
| 4 | Mẫu Nến | Tín hiệu đảo chiều tại vùng hỗ trợ/kháng cự |
| 5 | TP/SL Động | Quản lý lệnh và rủi ro |
| 6 | Phân tích AI | Tổng hợp khuyến nghị theo thời gian thực |
| 7 | Backtest | Kiểm thử hiệu quả lịch sử |

---

# PHẦN 2: CÀI ĐẶT VÀ THIẾT LẬP BAN ĐẦU

## 2.1. Cài Đặt Chung

Khi mở chỉ báo lần đầu, vào phần **Cài đặt chung** và điều chỉnh:

### Giao diện (Theme)
- **Light (Sáng):** Phù hợp với màn hình ban ngày, nền trắng
- **Dark (Tối):** Phù hợp với giao dịch ban đêm, bảo vệ mắt

### Hiển thị tín hiệu
- **Hiển thị bảng tín hiệu:** Bật để xem bảng thông tin góc phải màn hình — luôn bật
- **Hiển thị tín hiệu Mua/Bán:** Bật để thấy mũi tên và nhãn giá MUA/BÁN trên biểu đồ
- **Hiển thị Phân tích trực tiếp:** Bật để xem hộp phân tích chi tiết bên cạnh SuperTrend
- **Đọc vị nến khi gần đỉnh/đáy:** Bật để nhận diện mẫu nến đảo chiều

### Ngưỡng gần đỉnh/đáy
Mặc định 3% — tức là khi giá cách đường SuperTrend dưới 3% thì chỉ báo sẽ nhận diện mẫu nến. Có thể tăng lên 4-5% với cổ phiếu biến động mạnh.

## 2.2. Thiết Lập Phong Cách Giao Dịch — Bước Quan Trọng Nhất

Đây là bước quyết định chiến lược vào lệnh. Vào mục **Tín hiệu** và chọn:

### Chế Độ Ngắn Hạn (KHUYẾN NGHỊ ĐỂ VÀO LỆNH SỚM)
- ATR = 7 phiên, Hệ số = 2.0
- SuperTrend **nhạy hơn**, đảo chiều **sớm hơn**
- Bắt được tín hiệu MUA ngay khi xu hướng mới bắt đầu hình thành
- **Nhược điểm:** Có thể bị nhiễu nhiều hơn, đảo chiều giả nhiều hơn

### Chế Độ Dài Hạn
- ATR = 10 phiên, Hệ số = 3.0
- SuperTrend **chậm hơn**, ổn định hơn
- Ít tín hiệu giả hơn nhưng vào lệnh muộn hơn

### Chế Độ Tùy Chỉnh
- Tự nhập ATR và hệ số theo ý muốn
- Chỉ dành cho người dùng có kinh nghiệm

### Lọc Sức Mạnh Khi Đảo Chiều
- **Tắt (mặc định):** Tất cả tín hiệu đảo chiều đều được ghi nhận
- **Bật:** Chỉ chấp nhận tín hiệu MUA khi Dual Score ≥ 60%, tín hiệu BÁN khi Dual Score ≤ 40%
- Bật lọc sức mạnh giúp loại bỏ tín hiệu yếu nhưng có thể bỏ lỡ tín hiệu sớm

---

# PHẦN 3: SUPERTREND — ĐƯỜNG XU HƯỚNG CHÍNH

## 3.1. SuperTrend Là Gì?

SuperTrend là đường xu hướng động được tính dựa trên ATR (Average True Range — Biên độ trung bình thực). Đây là xương sống của toàn bộ chỉ báo, quyết định khi nào MUA, khi nào BÁN.

**Nguyên lý hoạt động:**
- Khi giá đóng cửa vượt lên trên đường SuperTrend → **Xu hướng TĂNG** (đường màu xanh)
- Khi giá đóng cửa xuống dưới đường SuperTrend → **Xu hướng GIẢM** (đường màu đỏ)
- Đường SuperTrend hoạt động như đường hỗ trợ động trong xu hướng tăng và kháng cự động trong xu hướng giảm

## 3.2. Cách Đọc SuperTrend Trên Biểu Đồ

**Màu sắc:**
- Đường xanh lá + vùng tô màu xanh nhạt: Đang trong xu hướng TĂNG → NẮM GIỮ hoặc MUA thêm
- Đường đỏ + vùng tô màu đỏ nhạt: Đang trong xu hướng GIẢM → ĐỨNG NGOÀI hoặc BÁN

**Ký hiệu đặc biệt:**
- Ký hiệu ✪ (ngôi sao) màu xanh: Đang trong xu hướng tăng, đây là mức hỗ trợ hiện tại
- Ký hiệu ✪ màu đỏ: Đang trong xu hướng giảm, đây là mức kháng cự hiện tại
- Ký hiệu 💎 màu vàng: Đây là Siêu Cổ Phiếu (xem phần 5)

## 3.3. Tín Hiệu MUA Và BÁN

**Tín hiệu MUA (▲):**
- Xuất hiện khi SuperTrend đảo chiều từ giảm sang tăng
- Hình tam giác xanh nhỏ bên dưới nến
- Nhãn "MUA" kèm giá mua tham khảo

**Tín hiệu BÁN (▼):**
- Xuất hiện khi SuperTrend đảo chiều từ tăng sang giảm
- Hình tam giác đỏ nhỏ bên trên nến
- Nhãn "BÁN" kèm giá bán tham khảo

## 3.4. Giá MUA/BÁN Tham Khảo — Ba Chế Độ

Chỉ báo cung cấp 3 cách tính giá vào lệnh (cấu hình trong phần Test lịch sử):

| Chế độ | Giá MUA | Giá BÁN | Ý nghĩa |
|--------|---------|---------|---------|
| 🏆 Lý tưởng nhất | Low (Đáy nến) | High (Đỉnh nến) | Giá tốt nhất có thể — hiếm đạt được |
| ⭐ Lý tưởng | HL2 (trung bình) | HL2 (trung bình) | Cân bằng giữa thực tế và lý tưởng |
| 📊 Thực tế | High (Đỉnh nến) | Low (Đáy nến) | Giá thực tế khi mua trên đỉnh, bán dưới đáy |

**Khuyến nghị:** Dùng chế độ "Lý tưởng" cho mục tiêu thực tế.

---

# PHẦN 4: DUAL SCORE — HỆ THỐNG ĐÁNH GIÁ SỨC MẠNH 9 ĐIỂM

## 4.1. Dual Score Là Gì?

Dual Score là hệ thống chấm điểm độc quyền của ManhDucCapital, đánh giá **sức mạnh thực sự** của xu hướng qua 9 tiêu chí kỹ thuật độc lập. Đây là lớp lọc thứ hai giúp xác nhận tín hiệu SuperTrend.

## 4.2. 9 Tiêu Chí Chấm Điểm Tăng (Bull Score)

Mỗi tiêu chí đúng = 1 điểm. Tối đa 9 điểm.

| # | Tiêu chí | Ý nghĩa |
|---|---------|---------|
| 1 | EMA9 > EMA21 | Đường ngắn hạn trên đường trung hạn → xu hướng tăng chính |
| 2 | Giá > VWAP | Giá đang cao hơn giá trung bình theo khối lượng ngày → tiền thông minh đang giữ |
| 3 | RSI (3-EMA) > 52 | Động lượng trên trung bình, tránh nhiễu vùng 50 |
| 4 | MACD Histogram > 0 và tăng | Động lực tăng đang tăng tốc, không chỉ dương |
| 5 | ADX > 20 | Xu hướng đủ mạnh, không phải sideway |
| 6 | OBV > EMA(OBV,10) | Tiền đang chảy vào cổ phiếu (On-Balance Volume) |
| 7 | Stoch tăng và chưa overbought (<80) | Đà tăng còn dư địa, chưa quá mua |
| 8 | Đóng cửa ở nửa trên nến | Phe mua kiểm soát phiên giao dịch |
| 9 | Volume > Trung bình 20 phiên | Khối lượng xác nhận xu hướng |

## 4.3. 9 Tiêu Chí Chấm Điểm Giảm (Bear Score)

Tương tự nhưng ngược chiều — mỗi tiêu chí giảm đúng = 1 điểm.

## 4.4. Cách Tính biasNorm — Điểm Sức Mạnh Tổng Hợp

```
Bull% = (Bull Score / 9) × 100
Bear% = (Bear Score / 9) × 100
NetBias = Bull% - Bear%
biasNorm = (NetBias + 100) / 2     → Chuẩn hóa về thang 0-100
```

**Thang đánh giá biasNorm:**

| Giá trị | Màu | Ý nghĩa | Hành động |
|---------|-----|---------|-----------|
| ≥ 75% | 🟣 Tím | RẤT MẠNH | Tiếp tục nắm giữ, có thể mua thêm |
| 55–74% | 🟢 Xanh | MẠNH | Nắm giữ, xu hướng ổn định |
| 45–54% | 🟡 Vàng | TRUNG BÌNH | Theo dõi, không tăng tỷ trọng |
| 25–44% | 🟠 Cam | YẾU | Chuẩn bị cắt giảm |
| < 25% | 🔴 Đỏ | RẤT YẾU | Thoát lệnh, bảo toàn vốn |

## 4.5. Mũi Tên Xu Hướng biasArrow

Bên cạnh điểm số, chỉ báo hiển thị mũi tên cho biết biasNorm đang thay đổi so với 3 phiên trước:
- **↑** : biasNorm tăng hơn 2 điểm → Sức mạnh đang tích lũy
- **↓** : biasNorm giảm hơn 2 điểm → Sức mạnh đang suy yếu
- **→** : biasNorm ổn định → Xu hướng đang duy trì

---

# PHẦN 5: SIÊU CỔ PHIẾU — LỌC CỔ PHIẾU ĐẲNG CẤP CAO

## 5.1. Siêu Cổ Phiếu Là Gì?

Siêu Cổ Phiếu (Super Stock) là khái niệm lấy cảm hứng từ phương pháp đầu tư của Mark Minervini và William O'Neil — những huyền thoại chứng khoán Mỹ. Đây là những cổ phiếu đang trong giai đoạn tăng trưởng vượt trội, có nền tảng xu hướng mạnh và thanh khoản tốt.

## 5.2. 7 Tiêu Chí Xét Siêu Cổ Phiếu

Cổ phiếu phải đạt **tối thiểu 5/7 tiêu chí** VÀ có thanh khoản đủ lớn:

| # | Tiêu chí | Ý nghĩa kinh tế |
|---|---------|----------------|
| 1 | Giá > EMA200 | Giá cao hơn trung bình 200 phiên → xu hướng dài hạn tăng |
| 2 | EMA200 đang tăng | Đường trung bình dài hạn hướng lên → nền tảng vĩ mô tích cực |
| 3 | Gần/phá đỉnh 52 tuần (≥90%) | Cổ phiếu mạnh nhất thị trường → momentum vượt trội |
| 4 | EMA50 > EMA200 (Golden Alignment) | Cấu trúc xu hướng hoàn hảo — ngắn hạn trên dài hạn |
| 5 | Giá > EMA50 | Giá trên đường trung bình trung hạn |
| 6 | ADX > 25 | Xu hướng cực mạnh, không phải sideway |
| 7 | Volume đang nở rộng (TB20 > TB60) | Tiền lớn đang tích lũy, khối lượng ngày càng tăng |

**Thanh khoản tối thiểu:** Bình quân 20 phiên ≥ 50 tỷ đồng/ngày (có thể điều chỉnh).

## 5.3. Cách Nhận Biết Siêu Cổ Phiếu Trên Biểu Đồ

- Nền biểu đồ chuyển màu vàng nhạt (nếu bật "Hiển thị nền Siêu cổ phiếu")
- Ký hiệu 💎 xuất hiện thay cho ✪
- Bảng tín hiệu góc phải hiển thị "💎 SIÊU CỔ PHIẾU" với nền vàng
- Hộp phân tích hiển thị tiêu chí chi tiết

## 5.4. Chiến Lược Với Siêu Cổ Phiếu

Khi phát hiện Siêu Cổ Phiếu với tín hiệu MUA:
1. **Ưu tiên cao nhất** — Đây là cơ hội giao dịch đặc biệt
2. Sử dụng câu châm ngôn của Mark Minervini: *"Great stocks make new highs, not new lows"*
3. Nguyên tắc: *"Buy high, sell higher"* — Đừng ngại mua khi cổ phiếu đang ở vùng đỉnh nếu đó là Siêu Cổ Phiếu
4. Nắm giữ lâu hơn bình thường — Siêu Cổ Phiếu thường có sóng tăng kéo dài hàng tháng
5. **Lưu ý quan trọng:** Chỉ báo KHÔNG hiển thị mẫu nến giảm với Siêu Cổ Phiếu (vì xu hướng tổng thể đang rất mạnh)

---

# PHẦN 6: BẢNG TÍN HIỆU GÓC PHẢI — TRUNG TÂM THÔNG TIN

## 6.1. Bảng Tín Hiệu Hiển Thị Gì?

Bảng tín hiệu góc trên bên phải là trung tâm thông tin quan trọng nhất, cập nhật liên tục trên mỗi phiên. Các thông tin hiển thị:

### Dòng Tiêu Đề
- Tên cổ phiếu (ticker)
- Nền vàng + icon 💎 nếu là Siêu Cổ Phiếu
- Nền xanh thông thường nếu cổ phiếu bình thường

### Xu Hướng Hiện Tại
- **▲ MUA VÀ NẮM GIỮ** (màu xanh): Đang trong xu hướng tăng → Tiếp tục giữ lệnh
- **▼ BÁN VÀ CHỜ ĐỢI** (màu đỏ): Đang trong xu hướng giảm → Đứng ngoài

### Thông Tin Lệnh
- **Ngày MUA/BÁN:** Ngày xuất hiện tín hiệu gần nhất
- **Giá MUA/BÁN:** Giá tại thời điểm tín hiệu (theo chế độ đã chọn)
- **Hỗ trợ / Kháng cự:** Giá trị đường SuperTrend hiện tại
- **Giữ lệnh:** Số phiên đã nắm giữ kể từ tín hiệu gần nhất

### Trạng Thái Lãi/Lỗ
- **ĐANG LÃI +X%**: Giá hiện tại cao hơn giá mua
- **ĐANG LỖ -X%**: Giá hiện tại thấp hơn giá mua
- **TRÁNH LỖ +X%**: Đang trong xu hướng bán, % thể hiện mức tránh được

### Trạng Thái Siêu Cổ Phiếu
- Điểm số X/7 tiêu chí
- Tooltip chi tiết từng tiêu chí khi hover chuột

## 6.2. Cách Đọc Và Sử Dụng Bảng Tín Hiệu

**Quy trình đọc bảng mỗi sáng:**
1. Xem dòng xu hướng — Đang "MUA VÀ NẮM GIỮ" hay "BÁN VÀ CHỜ ĐỢI"?
2. Kiểm tra số phiên giữ lệnh — Lệnh mới hay đã lâu?
3. Xem % lãi/lỗ — Lệnh đang hoạt động như thế nào?
4. Kiểm tra mức hỗ trợ/kháng cự — Giá còn xa hay gần đường SuperTrend?

---

# PHẦN 7: HỘP PHÂN TÍCH AI — KHUYẾN NGHỊ THEO THỜI GIAN THỰC

## 7.1. Hộp Phân Tích Là Gì?

Hộp phân tích (Analysis Label) là khối thông tin tổng hợp nổi bên cạnh đường SuperTrend, cập nhật theo từng phiên. Đây là "não bộ" của chỉ báo, tổng hợp tất cả thông tin thành một khuyến nghị hành động rõ ràng.

## 7.2. Cấu Trúc Hộp Phân Tích

### Dòng 1: Tóm Tắt Chỉ Báo Kỹ Thuật
```
RSI:XX↑  MACD:↑  ADX:XX💪  OBV:↑
EMA:9>21↑  VWAP:↑
```

**Đọc nhanh:**
- Mũi tên ↑ = tín hiệu tăng, ↓ = tín hiệu giảm, → = trung tính
- 💪 sau ADX: Xu hướng mạnh (ADX > 20)
- 😴 sau ADX: Xu hướng yếu, thị trường sideway

### Dòng 2: Phân Tích Khối Lượng
```
Mua: 15.2M | Bán: 8.7M | Delta: +54.3%
Vol/TB20: 🔥 2.3x — Đột biến!
Thanh khoản: 125.50 tỷ/ngày
```

**Ý nghĩa:**
- **Mua / Bán:** Tổng khối lượng nến xanh/đỏ từ khi xu hướng bắt đầu
- **Delta%:** Chênh lệch giữa khối lượng mua và bán theo %
  - Delta dương (+): Tiền mua nhiều hơn bán → lực cầu mạnh
  - Delta âm (-): Tiền bán nhiều hơn mua → áp lực cung lớn
- **Vol/TB20:** So sánh khối lượng hiện tại với trung bình 20 phiên
  - 🔥 ≥ 2.0x: Đột biến khối lượng — tín hiệu bứt phá quan trọng
  - ⚡ 1.5-2.0x: Khối lượng cao — lực mua/bán mạnh
  - 0.8-1.5x: Bình thường
  - < 0.8x: Khối lượng thấp — cẩn trọng với tín hiệu

### Dòng 3: Sức Mạnh Tổng Hợp
```
⚡Sức mạnh: MẠNH (68%) ↑
```
Đây là điểm biasNorm với mũi tên xu hướng.

### Dòng 4: Trạng Thái Xu Hướng
```
💰 Xu hướng: Tăng
```
hoặc
```
💸 Xu hướng: Giảm
```

### Dòng 5: Mục Tiêu Lợi Nhuận (Chỉ hiện khi đang có lệnh MUA)

Chỉ báo tự động tính 3 mục tiêu lợi nhuận dựa trên giá vào lệnh:
```
🎯 T1 nhắm đến: 28.5 — Có thể chốt 1/3 vị thế
```
hoặc khi đạt mục tiêu:
```
🎯 Đã đạt T1 (+20%) tại 28.5
👉 Gợi ý chốt 30%, để phần còn lại chạy đến T2: 33.6
```

**Ba mục tiêu cố định:**
- **T1 = +20%** từ giá mua → Chốt khoảng 30% vị thế
- **T2 = +40%** từ giá mua → Chốt thêm 40% vị thế
- **T3 = +80%** từ giá mua → Cân nhắc chốt toàn bộ hoặc dùng trailing stop

### Dòng 6: Khuyến Nghị Hành Động

Đây là dòng quan trọng nhất — chỉ báo đưa ra khuyến nghị cụ thể:

| Tình huống | Khuyến nghị |
|-----------|-------------|
| Vừa có tín hiệu MUA | 🚀 Tín hiệu bứt phá xác nhận → Thời điểm mở vị thế tốt |
| Vừa có tín hiệu BÁN | ⚠️ Xu hướng đảo chiều → Nên thoát hàng, bảo toàn vốn |
| Đang tăng, tiền vào tốt | 📈 Dòng tiền vào tốt, xu hướng ủng hộ → Tiếp tục nắm giữ |
| Đang tăng nhưng chậm | 👀 Momentum đang chậm lại → Giữ nhưng quan sát kỹ |
| Đang tăng nhưng sức yếu | ⚠️ Tín hiệu chưa đồng thuận → Chưa nên tăng tỷ trọng |
| Đang giảm, bán áp đảo | ⛔ Áp lực bán còn chiếm ưu thế → Đứng ngoài quan sát |
| Đang giảm nhưng hồi kỹ thuật | 🔍 Có dấu hiệu hồi kỹ thuật → Chưa đủ xác nhận, chờ thêm |
| Thị trường tích lũy | ⚖️ Thị trường tích lũy → Chờ breakout mới vào lệnh |

---

# PHẦN 8: VỊ NẾN — NHẬN DIỆN MẪU NẾN TẠI VÙNG THEN CHỐT

## 8.1. Tại Sao Phân Tích Mẫu Nến?

Mẫu nến là ngôn ngữ của thị trường — chúng cho biết phe mua và phe bán đang "nói chuyện" gì với nhau tại các vùng giá quan trọng. Chỉ báo chỉ nhận diện mẫu nến khi giá **đang gần đường SuperTrend** (trong ngưỡng 3% mặc định) — vì đây là thời điểm quyết định nhất.

## 8.2. Các Mẫu Nến Đảo Chiều TĂNG (Khi Tiếp Cận Vùng Hỗ Trợ)

### 🌟 Morning Star (Sao Mai) — Mạnh nhất
**3 nến:** Nến đỏ lớn → Nến nhỏ (doji hoặc spinning top) → Nến xanh vượt giữa thân nến đầu

**Ý nghĩa:** Phe bán kiệt sức, phe mua chiếm quyền kiểm soát hoàn toàn.

**Cách giao dịch:** Mua tại mở cửa nến xanh thứ tư. Xác nhận bởi khối lượng cao.

### 🔨 Hammer (Búa) — Rất mạnh
**Đặc điểm:** Đuôi dưới dài ≥ 2× thân nến, đuôi trên ngắn ≤ 0.5× thân

**Ý nghĩa:** Phe bán đẩy giá xuống thấp nhưng phe mua phản công mạnh, kéo giá trở lên.

**Cách giao dịch:** Mua khi nến tiếp theo đóng cửa cao hơn đỉnh nến Hammer.

### 🕯️ Bullish Engulfing (Nến Nhấn Chìm Tăng) — Rất mạnh
**Đặc điểm:** Nến xanh bao trùm toàn bộ thân nến đỏ trước đó

**Ý nghĩa:** Lực mua áp đảo hoàn toàn, đảo chiều tăng tại vùng hỗ trợ.

**Cách giao dịch:** Mua khi nến nhấn chìm tăng đóng cửa. Cần volume cao hơn nến trước.

### 🌤️ Piercing Line (Đường Xuyên) — Mạnh
**Đặc điểm:** Nến xanh mở dưới đáy nến đỏ trước, đóng cửa vượt quá nửa thân nến đỏ

**Ý nghĩa:** Phe mua phản công mạnh tại vùng hỗ trợ, nhưng chưa hoàn toàn áp đảo.

### 🌅 Dragonfly Doji (Doji Chuồn Chuồn) — Mạnh
**Đặc điểm:** Giá mở = giá đóng, đuôi dưới rất dài

**Ý nghĩa:** Phe bán đã đẩy giá xuống thấp nhưng thất bại, phe mua đưa giá về mức mở.

### 〰️ Doji — Trung bình
**Đặc điểm:** Giá mở và đóng gần bằng nhau, thân nến cực nhỏ

**Ý nghĩa:** Thị trường do dự, cân bằng sức mạnh. Chờ nến tiếp theo xác nhận.

### 🔃 Inverted Hammer (Búa Ngược)
**Đặc điểm:** Đuôi trên dài ≥ 2× thân, xuất hiện TẠI ĐÁY

**Ý nghĩa:** Phe mua đã thăm dò đỉnh cao hơn. Cần xác nhận bởi nến xanh tiếp theo.

### 🪖 Three White Soldiers (Ba Chiến Binh) — Cực mạnh
**Đặc điểm:** 3 nến xanh liên tiếp, mỗi nến mở trong thân nến trước và đóng cao dần

**Ý nghĩa:** Phe mua kiên định và bền vững — xu hướng tăng có nền tảng chắc chắn.

### 🔧 Tweezer Bottom (Kẹp Đáy) — Mạnh
**Đặc điểm:** 2 nến (đỏ rồi xanh) có đáy gần bằng nhau, chênh lệch < 0.3%

**Ý nghĩa:** Vùng hỗ trợ được test 2 lần — phe bán không thể phá vỡ, phe mua vững chắc.

### 📦 Inside Bar (Nến Nằm Trong) — Tiếp theo breakout
**Đặc điểm:** Toàn bộ biên độ nến nằm trong nến trước

**Ý nghĩa tại hỗ trợ:** Áp lực bán suy yếu, thị trường tích lũy. Mua khi giá vượt đỉnh nến mẹ.

### 💚 Marubozu Tăng — Mạnh
**Đặc điểm:** Nến xanh thân dài, gần như không có đuôi (thân/biên độ ≥ 60%)

**Ý nghĩa:** Phe mua kiểm soát hoàn toàn phiên giao dịch từ đầu đến cuối.

## 8.3. Các Mẫu Nến Đảo Chiều GIẢM (Khi Tiếp Cận Vùng Kháng Cự)

### 🌆 Evening Star (Sao Hôm) — Mạnh nhất
**3 nến:** Nến xanh lớn → Nến nhỏ → Nến đỏ xuống dưới giữa thân nến đầu

**Ý nghĩa:** Phe mua kiệt sức, phe bán chiếm quyền kiểm soát.

**Cách giao dịch:** Cân nhắc chốt lời hoặc giảm tỷ trọng.

### ⭐ Shooting Star (Sao Băng) — Rất mạnh
**Đặc điểm:** Đuôi trên dài ≥ 2× thân, xuất hiện TẠI ĐỈNH

**Ý nghĩa:** Phe mua đẩy giá lên cao nhưng thất bại, phe bán kéo giá xuống mạnh.

### 🕯️ Bearish Engulfing (Nến Nhấn Chìm Giảm) — Rất mạnh
**Đặc điểm:** Nến đỏ bao trùm toàn bộ thân nến xanh trước

**Ý nghĩa:** Áp lực bán áp đảo hoàn toàn tại vùng kháng cự.

### 🪝 Hanging Man (Người Treo Cổ) — Mạnh
**Đặc điểm:** Giống Hammer nhưng xuất hiện TẠI ĐỈNH

**Ý nghĩa:** Dấu hiệu cảnh báo — phe bán đang thăm dò vùng giá thấp hơn.

### 🌃 Gravestone Doji (Doji Bia Mộ) — Mạnh
**Đặc điểm:** Giá mở = giá đóng, đuôi trên rất dài

**Ý nghĩa:** Phe mua đẩy giá lên cao nhưng thất bại, phe bán kéo lại về mức mở.

### 🌧️ Dark Cloud Cover (Mây Đen) — Mạnh
**Đặc điểm:** Nến đỏ mở cao hơn thân nến xanh, đóng dưới giữa thân nến xanh

### 🪽 Three Black Crows (Ba Con Quạ) — Cực mạnh
**Đặc điểm:** 3 nến đỏ liên tiếp, đóng dần thấp hơn

**Ý nghĩa:** Áp lực bán bền vững và mạnh mẽ.

### 🔧 Tweezer Top (Kẹp Đỉnh) — Mạnh
**Đặc điểm:** 2 nến (xanh rồi đỏ) có đỉnh gần bằng nhau

**Ý nghĩa:** Kháng cự được test 2 lần — phe mua không thể phá vỡ.

## 8.4. Quy Tắc Sử Dụng Mẫu Nến

**Nguyên tắc vàng:**
1. Mẫu nến chỉ có giá trị khi xuất hiện TẠI các vùng then chốt (hỗ trợ/kháng cự)
2. Luôn cần xác nhận từ nến tiếp theo trước khi hành động
3. Khối lượng giao dịch cao = tín hiệu mạnh hơn
4. Kết hợp với biasNorm để có bức tranh toàn diện hơn

**Lỗi thường gặp:**
- Không được giao dịch chỉ dựa trên một mẫu nến duy nhất
- Không bỏ qua xu hướng tổng thể (SuperTrend) để chỉ nhìn mẫu nến

---

# PHẦN 9: TP/SL ĐỘNG — QUẢN LÝ RỦI RO VÀ LỢI NHUẬN

## 9.1. Hệ Thống TP/SL Là Gì?

TP/SL (Take Profit / Stop Loss) là hệ thống quản lý lệnh tự động, tính toán các mức chốt lời và cắt lỗ dựa trên điều kiện thị trường thực tế.

## 9.2. Cách Hệ Thống TP/SL Hoạt Động

**Kích hoạt:** Khi bật "Hiển thị đường TP/SL" và có tín hiệu MUA, chỉ báo tự động vẽ 5 đường ngang:

| Đường | Màu | Ý nghĩa |
|-------|-----|---------|
| 📌 Vào lệnh | Vàng | Giá vào lệnh tham khảo |
| 🛑 Stop Loss | Đỏ | Mức cắt lỗ — Thoát ngay khi giá chạm |
| 🎯 T1 | Xanh nhạt | Mục tiêu chốt lời 1 (RR mặc định 1:1) |
| 🎯 T2 | Xanh | Mục tiêu chốt lời 2 (RR mặc định 1:2) |
| 🎯 T3 | Xanh đậm | Mục tiêu chốt lời 3 (RR mặc định 1:3) |

## 9.3. Cách Tính Stop Loss

Stop Loss được tính theo công thức:

```
SL = MIN(Pivot Low gần nhất, Giá vào lệnh) - (Hệ số SL × ATR)
Risk = Giá vào lệnh - SL
```

**Cài đặt SL Buffer (Hệ số ATR):**
- Mặc định: 1.5× ATR dưới Pivot Low gần nhất
- Tăng hệ số → SL rộng hơn, ít bị quét hơn nhưng lỗ nhiều hơn nếu kích hoạt
- Giảm hệ số → SL chặt hơn, bảo vệ vốn tốt hơn nhưng dễ bị quét nhiễu

**Pivot Low:**
- Là đáy cục bộ trong vòng N nến (mặc định 10 nến mỗi bên)
- Đây là vùng "phe mua đã bảo vệ giá" trong quá khứ gần đây

## 9.4. Cách Tính Take Profit (TP)

```
TP1 = Giá vào lệnh + Risk × R1  (mặc định R1=1.0)
TP2 = Giá vào lệnh + Risk × R2  (mặc định R2=2.0)
TP3 = Giá vào lệnh + Risk × R3  (mặc định R3=3.0)
```

**Ví dụ thực tế:**
- Giá vào: 25,000 VNĐ
- SL: 23,500 VNĐ → Risk = 1,500 VNĐ (6%)
- TP1 = 25,000 + 1,500 × 1 = 26,500 (+6%) → Chốt 30% vị thế
- TP2 = 25,000 + 1,500 × 2 = 28,000 (+12%) → Chốt thêm 40%
- TP3 = 25,000 + 1,500 × 3 = 29,500 (+18%) → Giữ 30% còn lại

## 9.5. Chiến Lược Chốt Lời Từng Phần

**Khuyến nghị phân bổ:**
- Khi đạt T1: Chốt 30% vị thế → Đảm bảo lệnh không lỗ
- Khi đạt T2: Chốt thêm 40% vị thế → Đã thu hồi phần lớn vốn + lãi
- Gồng 30% còn lại đến T3 hoặc khi có tín hiệu BÁN

**Kết hợp với chiến lược "Vào sớm — Giữ dài":**
- Có thể điều chỉnh tỷ lệ chốt T1/T2 nhỏ hơn (ví dụ 20%/20%) để giữ lại nhiều hơn cho sóng dài

---

# PHẦN 10: CHIẾN LƯỢC GIAO DỊCH CHÍNH — VÀO SỚM, GIỮ DÀI

## 10.1. Tổng Quan Chiến Lược

Chiến lược "Vào sớm bằng ngắn hạn, giữ theo dài hạn" là phương pháp tối ưu kết hợp hai thế mạnh:
- **Ngắn hạn:** Nhạy bén, bắt tín hiệu sớm, vào lệnh ở giá tốt hơn
- **Dài hạn:** Kiên nhẫn, nắm giữ qua biến động, tối đa hóa lợi nhuận từ xu hướng lớn

## 10.2. Điều Kiện Lý Tưởng Để Vào Lệnh

Để vào lệnh với xác suất thành công cao nhất, cần đồng thời:

### Điều Kiện Bắt Buộc (Cả 3 phải thỏa mãn)
1. **Tín hiệu MUA** từ SuperTrend (tam giác xanh ▲)
2. **biasNorm ≥ 55%** (Sức mạnh ở mức MẠNH trở lên)
3. **Delta Volume dương** (Tiền mua > tiền bán kể từ khi xu hướng bắt đầu)

### Điều Kiện Tăng Cường (Càng nhiều càng tốt)
- ADX > 25 (Xu hướng đặc biệt mạnh)
- Volume hiện tại ≥ 1.5× trung bình 20 phiên
- Xuất hiện mẫu nến tăng mạnh (Morning Star, Bullish Engulfing)
- Là Siêu Cổ Phiếu (isSuperStock = true)
- biasNorm đang có mũi tên ↑

## 10.3. Kịch Bản Vào Lệnh Ngắn Hạn

**Thiết lập chỉ báo:**
- Phong cách giao dịch: **Ngắn hạn** (ATR=7, Hệ số=2)
- Lọc sức mạnh: **Tắt** (để nhận tín hiệu sớm hơn)

**Quy trình vào lệnh:**

**Bước 1 — Quét danh mục:**
Mỗi ngày sáng hoặc tối, lọc danh sách cổ phiếu theo:
- Đang trong xu hướng TĂNG (SuperTrend xanh)
- biasNorm ≥ 55%
- Ưu tiên Siêu Cổ Phiếu

**Bước 2 — Chờ tín hiệu:**
Không vào lệnh khi không có tín hiệu. Chờ tam giác MUA ▲ xuất hiện.

**Bước 3 — Xác nhận 3 lớp:**
1. SuperTrend vừa đảo chiều tăng → ✅
2. biasNorm ≥ 55% và đang ↑ → ✅
3. Delta Volume dương → ✅

**Bước 4 — Xác định giá vào:**
Dùng giá đóng cửa của nến tín hiệu hoặc mở cửa nến tiếp theo.

**Bước 5 — Đặt Stop Loss:**
Bật TP/SL để xem đường SL tự động. Không được bỏ qua bước này.

**Bước 6 — Phân bổ vốn:**
Không bao giờ đặt quá 30% tổng danh mục vào một cổ phiếu đơn lẻ.

## 10.4. Kịch Bản Nắm Giữ Dài Hạn

Sau khi vào lệnh theo ngắn hạn, chuyển sang tư duy dài hạn:

**Những gì KHÔNG nên làm:**
- Không hoảng loạn khi giá điều chỉnh 3-5% nếu SuperTrend vẫn xanh
- Không thoát lệnh chỉ vì biasNorm giảm tạm thời về 50-55%
- Không nhìn biểu đồ hàng giờ — điều này gây ra quyết định cảm tính

**Những gì NÊN làm:**
- Kiểm tra chỉ báo 1 lần/ngày, tốt nhất sau giờ đóng cửa phiên
- Giữ lệnh khi: SuperTrend còn xanh + biasNorm ≥ 45%
- Chốt T1/T2 từng phần theo kế hoạch đã đặt
- Gồng phần còn lại cho sóng dài

**Tín hiệu cảnh báo cần theo dõi:**
- biasNorm giảm dưới 45% và có mũi tên ↓
- Delta Volume chuyển âm
- ADX giảm dưới 20
- Xuất hiện mẫu nến đảo chiều giảm tại kháng cự

## 10.5. Điều Kiện Thoát Lệnh

**Thoát ngay lập tức khi:**
- Giá chạm Stop Loss
- SuperTrend đảo chiều BÁN (tam giác đỏ ▼)

**Cân nhắc thoát một phần khi:**
- biasNorm giảm xuống 45-55% sau khi đã đạt T1
- Volume đột biến giảm (< 0.5× trung bình)
- Xuất hiện mẫu nến đảo chiều mạnh tại kháng cự

**Không thoát khi:**
- SuperTrend vẫn xanh và biasNorm ≥ 55% → Tiếp tục nắm giữ
- Có điều chỉnh tạm thời nhưng cấu trúc xu hướng chưa phá vỡ

---

# PHẦN 11: BACKTEST — KIỂM THỬ HIỆU QUẢ LỊCH SỬ

## 11.1. Backtest Là Gì Và Tại Sao Cần?

Backtest là tính năng mô phỏng lại lịch sử giao dịch dựa trên tín hiệu của chỉ báo. Nó giúp:
- Đánh giá hiệu quả của chỉ báo trên cổ phiếu cụ thể
- Xem tỷ lệ thắng/thua trong lịch sử
- Tính toán lợi nhuận tiềm năng
- Phân tích số phiên nắm giữ trung bình

## 11.2. Cách Kích Hoạt Và Cấu Hình Backtest

**Bật tính năng:** Vào mục **Test lịch sử** → Bật "Hiển thị bảng lịch sử giao dịch"

**Các cài đặt quan trọng:**

| Cài đặt | Giá trị | Gợi ý |
|---------|---------|-------|
| Từ ngày | Ngày bắt đầu | Nên test ít nhất 1-2 năm |
| Vốn ban đầu | Triệu VNĐ | Nhập số vốn thực tế của bạn |
| Tính lãi kép | Bật/Tắt | Bật = thực tế hơn (vốn xoay vòng) |
| Kiểu điểm Mua/Bán | Lý tưởng | Cân bằng giữa thực tế và lý tưởng |

## 11.3. Đọc Bảng Backtest

Bảng lịch sử hiển thị từng lệnh giao dịch với:
- Ngày mua / Giá mua
- Ngày bán / Giá bán
- Số phiên giữ lệnh
- Lãi/lỗ theo số tiền và phần trăm

**Dòng tổng kết cuối bảng:**
- Tổng lãi/lỗ của tất cả lệnh
- ROI (Return on Investment) tổng
- Win Rate (Tỷ lệ thắng)
- Average PnL (Lãi/lỗ trung bình mỗi lệnh)
- Max Drawdown (Mức thua lỗ lớn nhất từ đỉnh vốn)

## 11.4. Cách Đánh Giá Kết Quả Backtest

**Backtest TỐT khi:**
- Win Rate ≥ 50%
- Average PnL dương
- Max Drawdown < 20%
- Có ít nhất 10-15 lệnh để đánh giá có ý nghĩa thống kê

**Cảnh báo:**
- Backtest chỉ dùng một vị thế tại một thời điểm (không tính giao dịch đồng thời nhiều cổ phiếu)
- Không tính phí giao dịch và thuế
- Kết quả quá khứ không đảm bảo hiệu suất tương lai

## 11.5. Sử Dụng Backtest Để Tối Ưu Chiến Lược

**Thử nghiệm các cài đặt:**
1. So sánh kết quả Ngắn hạn vs Dài hạn trên cùng một cổ phiếu
2. Thử bật/tắt Lọc sức mạnh để xem tác động
3. Kiểm tra nhiều khung thời gian (D, W)
4. Chọn giai đoạn thị trường khác nhau (bull market, bear market, sideway)

---

# PHẦN 12: BOLLINGER BAND — CÔNG CỤ ĐO BIẾN ĐỘNG

## 12.1. Bollinger Band Trong ManhDucCapital

Bollinger Band (BB) là công cụ phụ trợ, mặc định tắt. Khi bật, màu sắc BB thay đổi theo xu hướng SuperTrend:
- **Xanh lá:** Đang trong xu hướng tăng
- **Đỏ:** Đang trong xu hướng giảm

## 12.2. Cách Sử Dụng Bollinger Band Kết Hợp

**3 tình huống chính:**

1. **Giá chạm Band Trên → Xem xét chốt lời một phần**
   - Giá đang "kéo căng" về phía trên, dễ điều chỉnh
   - Không nhất thiết bán toàn bộ nếu biasNorm vẫn mạnh

2. **Giá chạm Band Dưới → Cơ hội mua thêm hoặc vào lệnh**
   - Chỉ có giá trị khi SuperTrend đang xanh
   - Kết hợp với mẫu nến tăng để xác nhận

3. **BB Co Hẹp (Squeeze) → Chuẩn bị bứt phá mạnh**
   - Hai band tiến lại gần nhau = thị trường đang tích lũy
   - Chờ breakout để xác định hướng

## 12.3. Cài Đặt Bollinger Band

- **Chu kỳ:** 20 (mặc định, dùng cho phân tích ngắn-trung hạn)
- **Độ lệch chuẩn:** 2.0 (bao phủ khoảng 95% biến động bình thường)

---

# PHẦN 13: EMA — ĐƯỜNG TRUNG BÌNH DI ĐỘNG

## 13.1. Ba Đường EMA Trong Chỉ Báo

Chỉ báo tích hợp 3 đường EMA tùy chỉnh (mặc định ẩn, cần bật trong phần cài đặt):
- **EMA 50** (Cam): Xu hướng trung hạn, 10-12 tuần giao dịch
- **EMA 100** (Xanh): Xu hướng dài hạn, 20-25 tuần
- **EMA 200** (Tím): Xu hướng vĩ mô, cả năm giao dịch

## 13.2. Ý Nghĩa Của Từng Đường EMA

**EMA50:** Nhà đầu tư tổ chức thường dùng EMA50 làm điểm mua vào trong xu hướng tăng. Giá test EMA50 và bật lên = cơ hội mua tốt.

**EMA100:** Ranh giới phân tách giữa xu hướng trung hạn tăng và giảm.

**EMA200:** Đường quan trọng nhất — vượt EMA200 và giữ trên = cổ phiếu chuyển sang bull market dài hạn.

## 13.3. Các Cấu Hình EMA Quan Trọng

**Golden Cross (Vàng):** EMA50 cắt lên trên EMA200 → Tín hiệu dài hạn rất mạnh.

**Death Cross (Chết):** EMA50 cắt xuống dưới EMA200 → Cảnh báo giảm dài hạn.

**Golden Alignment (Cấu hình vàng):** Giá > EMA50 > EMA200 → Cấu trúc hoàn hảo nhất cho giao dịch theo xu hướng.

---

# PHẦN 14: CẢNH BÁO (ALERTS) — KHÔNG BỎ LỠ TÍN HIỆU

## 14.1. Ba Loại Cảnh Báo Có Sẵn

ManhDucCapital cung cấp 3 cảnh báo tự động:

| Cảnh báo | Khi nào kích hoạt | Hành động |
|---------|-------------------|-----------|
| Cảnh báo điểm Mua | SuperTrend đảo chiều tăng | Kiểm tra và quyết định vào lệnh |
| Cảnh báo điểm Bán | SuperTrend đảo chiều giảm | Kiểm tra và quyết định thoát lệnh |
| Cảnh báo đảo chiều | Bất kỳ đảo chiều nào | Cập nhật nhanh trạng thái |

## 14.2. Cách Thiết Lập Cảnh Báo Trên TradingView

1. Nhấp chuột phải vào biểu đồ → "Add Alert"
2. Điều kiện: Chọn từ dropdown "ManhDucCapital-0973124824"
3. Chọn loại cảnh báo: "Cảnh báo điểm Mua"
4. Kênh nhận: Email, SMS, hoặc Push notification
5. Lặp lại cho cổ phiếu bạn muốn theo dõi

---

# PHẦN 15: QUY TRÌNH GIAO DỊCH HÀNG NGÀY

## 15.1. Quy Trình Buổi Sáng (Trước 9:00)

1. **Quét danh mục (5 phút):**
   - Lọc các cổ phiếu đang trong xu hướng TĂNG
   - Ưu tiên Siêu Cổ Phiếu
   - Đánh dấu cổ phiếu có biasNorm ≥ 55%

2. **Kiểm tra lệnh đang giữ (3 phút):**
   - SuperTrend có còn xanh không?
   - biasNorm đang ở mức nào?
   - Đã đạt T1 hay T2 chưa?

3. **Lập kế hoạch phiên (2 phút):**
   - Cổ phiếu nào cần đặt cảnh báo?
   - Có mẫu nến nào đặc biệt tối qua?

## 15.2. Quy Trình Theo Dõi Trong Phiên

- Không nhìn biểu đồ liên tục — gây ra quyết định cảm tính
- Chỉ kiểm tra khi có cảnh báo hoặc mỗi 1-2 tiếng
- Tập trung vào giá cuối phiên, không phải giá trong phiên

## 15.3. Quy Trình Buổi Tối (Sau 15:00)

1. **Cập nhật chỉ báo:**
   - Xem hộp phân tích cho tất cả vị thế
   - Đọc khuyến nghị "Action"
   - Kiểm tra mẫu nến hôm nay

2. **Ghi chép nhật ký:**
   - Tín hiệu nào xuất hiện hôm nay?
   - Quyết định gì đã thực hiện?
   - Kết quả so với kỳ vọng?

3. **Chuẩn bị ngày mai:**
   - Cổ phiếu nào sắp tiếp cận đường SuperTrend?
   - biasNorm đang thay đổi theo chiều nào?

---

# PHẦN 16: QUẢN LÝ VỐN VÀ RỦI RO

## 16.1. Nguyên Tắc Phân Bổ Vốn

**Quy tắc 30%:** Không bao giờ đặt quá 30% tổng danh mục vào một cổ phiếu duy nhất.

**Quy tắc 2%:** Mỗi lệnh giao dịch, số tiền thua lỗ tối đa (nếu SL kích hoạt) không được vượt quá 2% tổng vốn.

**Ví dụ áp dụng:**
- Tổng vốn: 500 triệu VNĐ
- Tối đa 2% rủi ro = 10 triệu VNĐ/lệnh
- Nếu SL cách giá vào 6% → Số tiền mua tối đa = 10 triệu / 6% = 167 triệu

## 16.2. Phân Tầng Vào Lệnh

Thay vì vào toàn bộ vị thế cùng một lúc, chia làm 2-3 lần:

**Lần 1 (50%):** Khi xuất hiện tín hiệu MUA + biasNorm ≥ 55%

**Lần 2 (30%):** Khi giá test lại đường SuperTrend (pullback) thành công + mẫu nến tăng

**Lần 3 (20%):** Khi giá breakout vượt đỉnh gần nhất với volume cao

## 16.3. Nguyên Tắc Cắt Lỗ

**Quy tắc vàng:** Bao giờ cũng đặt lệnh cắt lỗ trước khi vào thị trường.

**Không bao giờ:**
- Giữ lệnh lỗ với hy vọng giá sẽ hồi
- Bỏ qua tín hiệu BÁN vì "cổ phiếu này tốt"
- Tăng vị thế khi đang thua lỗ

---

# PHẦN 17: CÁC LỖI PHỔ BIẾN VÀ CÁCH TRÁNH

## Lỗi 1: Vào Lệnh Ngay Khi Thấy Tín Hiệu Mà Không Xác Nhận

**Triệu chứng:** Mua ngay khi tam giác MUA xuất hiện mà không kiểm tra biasNorm và volume.

**Giải pháp:** Luôn xác nhận 3 điều kiện: SuperTrend + biasNorm ≥ 55% + Delta Volume dương.

## Lỗi 2: Không Đặt Stop Loss

**Triệu chứng:** Giữ lệnh lỗ ngày càng sâu vì "chắc chắn sẽ hồi."

**Giải pháp:** Bật TP/SL và tuân thủ tuyệt đối. Xem hộp phân tích để biết mức SL tự động.

## Lỗi 3: Thoát Lệnh Sớm Vì Biến Động Bình Thường

**Triệu chứng:** Bán ngay khi giá điều chỉnh 3-5% mặc dù SuperTrend vẫn xanh.

**Giải pháp:** Chỉ thoát khi SuperTrend đảo chiều hoặc SL bị kích hoạt. Điều chỉnh tạm thời là bình thường trong xu hướng tăng.

## Lỗi 4: Bỏ Qua Tín Hiệu BÁN Vì "Cổ Phiếu Tốt"

**Triệu chứng:** Giữ cổ phiếu qua xu hướng giảm vì tin vào câu chuyện cơ bản.

**Giải pháp:** Chỉ báo kỹ thuật phản ánh hành động giá thực tế. Khi SuperTrend đỏ, xu hướng đã thay đổi.

## Lỗi 5: Giao Dịch Quá Nhiều

**Triệu chứng:** Vào/ra lệnh mỗi ngày, phí giao dịch ăn hết lợi nhuận.

**Giải pháp:** Chỉ giao dịch khi có tín hiệu rõ ràng. Chiến lược "Vào sớm — Giữ dài" đòi hỏi kiên nhẫn.

## Lỗi 6: Không Đa Dạng Hóa

**Triệu chứng:** Đặt tất cả vốn vào 1-2 cổ phiếu.

**Giải pháp:** Phân tán vào 5-10 cổ phiếu, không quá 30% mỗi cổ phiếu.

---

# PHẦN 18: VÍ DỤ GIAO DỊCH THỰC TẾ

## 18.1. Tình Huống 1: Siêu Cổ Phiếu Bứt Phá

**Bối cảnh:**
- Cổ phiếu XYZ đang hiển thị 💎 (Siêu Cổ Phiếu 6/7 tiêu chí)
- SuperTrend vừa đảo chiều tăng (tam giác xanh ▲)
- biasNorm = 72% (MẠNH, mũi tên ↑)
- Volume 2.3× trung bình 20 phiên (🔥 Đột biến)
- Delta Volume: +65% (tiền mua áp đảo)
- Khuyến nghị: "🚀 Tín hiệu bứt phá xác nhận → Thời điểm mở vị thế tốt"

**Hành động:**
1. Vào 50% vị thế ở giá đóng cửa (ví dụ: 25,000)
2. Đặt SL theo hệ thống (ví dụ: 23,200 = -7.2%)
3. TP1 = 30,000 (+20%), TP2 = 35,000 (+40%), TP3 = 45,000 (+80%)

**Quản lý lệnh:**
- Tuần 1-2: Giữ nguyên, theo dõi biasNorm hàng ngày
- Khi đạt T1 (+20%): Chốt 30% → "Lock" lợi nhuận
- Nếu Siêu Cổ Phiếu vẫn duy trì: Giữ 70% còn lại cho sóng lớn
- Thoát toàn bộ khi SuperTrend đảo chiều đỏ

## 18.2. Tình Huống 2: Tín Hiệu Cần Chờ Thêm

**Bối cảnh:**
- SuperTrend vừa đảo chiều tăng (tam giác xanh ▲)
- biasNorm = 48% (TRUNG BÌNH)
- Delta Volume: +10% (nhỏ, chưa rõ ràng)
- Khuyến nghị: "⚠️ Tín hiệu chưa đồng thuận → Chưa nên tăng tỷ trọng"

**Hành động:** KHÔNG vào lệnh. Chờ biasNorm tăng lên ≥ 55% và Delta Volume rõ ràng hơn.

**Bài học:** Không phải tín hiệu MUA nào cũng đáng giao dịch. Chất lượng quan trọng hơn số lượng.

## 18.3. Tình Huống 3: Xử Lý Điều Chỉnh Trong Xu Hướng Tăng

**Bối cảnh:**
- Đang giữ lệnh lãi +25% từ 2 tuần trước
- Hôm nay giá điều chỉnh -5%, mẫu nến Doji xuất hiện gần đường SuperTrend
- biasNorm = 58% (vẫn MẠNH nhưng giảm từ 70%)
- SuperTrend vẫn xanh
- Khuyến nghị: "👀 Momentum đang chậm lại → Giữ nhưng quan sát kỹ"

**Hành động:**
- Không thoát lệnh — SuperTrend chưa đảo chiều
- Theo dõi kỹ 2-3 phiên tiếp theo
- Nếu biasNorm về dưới 45%: Cân nhắc chốt thêm 20%
- Nếu giá bật lại với volume mạnh: Tiếp tục nắm giữ

---

# PHẦN 19: CÂU HỎI THƯỜNG GẶP

## Q1: Chỉ báo hoạt động tốt với khung thời gian nào?

**A:** Chỉ báo hoạt động tốt nhất trên khung **Daily (D)** cho chiến lược "Vào sớm — Giữ dài." Cũng có thể dùng trên khung **Weekly (W)** để lọc xu hướng vĩ mô.

Với chiến lược ngắn hạn hơn, có thể dùng khung 4H nhưng cần điều chỉnh lại cài đặt ATR.

## Q2: Tôi có nên bật tất cả tính năng không?

**A:** Không cần thiết. Gợi ý thiết lập tối ưu:
- ✅ Bảng tín hiệu: Luôn bật
- ✅ Tín hiệu Mua/Bán: Luôn bật
- ✅ Phân tích trực tiếp: Luôn bật
- ✅ Đọc vị nến: Luôn bật
- ⚡ TP/SL: Bật khi đang có lệnh
- 📊 Siêu cổ phiếu: Bật khi muốn lọc
- 📉 Backtest: Chỉ bật khi cần phân tích

## Q3: biasNorm 60% nhưng SuperTrend đỏ — Có nên mua không?

**A:** Không. SuperTrend là tín hiệu chính, biasNorm là tín hiệu xác nhận. Khi SuperTrend đỏ = xu hướng giảm = không mua. biasNorm cao trong xu hướng giảm chỉ có nghĩa đà giảm có thể chậm lại, chưa đủ để vào lệnh.

## Q4: Lọc sức mạnh có nên bật không?

**A:** Tùy chiến lược:
- **Tắt (mặc định):** Bắt tín hiệu sớm hơn, phù hợp với chiến lược vào sớm
- **Bật:** Lọc bỏ tín hiệu yếu, ít lệnh hơn nhưng chất lượng cao hơn

Khuyến nghị: Thử backtest cả hai chế độ trên cổ phiếu bạn giao dịch để tìm cài đặt phù hợp.

## Q5: Khi nào nên bổ sung vị thế?

**A:** Thêm vị thế khi:
- Đang có lãi (không bao giờ thêm khi đang lỗ)
- SuperTrend vẫn xanh
- biasNorm ≥ 60%
- Giá test lại đường SuperTrend thành công (pullback)
- Volume xác nhận (≥ 1.0× trung bình)

## Q6: Tại sao Siêu Cổ Phiếu không hiển thị mẫu nến giảm?

**A:** Đây là thiết kế có chủ đích. Siêu Cổ Phiếu đang trong xu hướng tăng mạnh dài hạn, những điều chỉnh kỹ thuật tạm thời là bình thường và không đáng để cảnh báo. Việc hiển thị mẫu nến giảm sẽ tạo ra lo lắng không cần thiết cho nhà đầu tư nắm giữ dài hạn.

---

# PHẦN 20: TỔNG KẾT VÀ LỜI KHUYÊN

## 20.1. Bốn Nguyên Tắc Sử Dụng ManhDucCapital

**Nguyên tắc 1 — Kiên nhẫn:**
Chỉ hành động khi đủ 3 điều kiện xác nhận. Không có cơ hội tốt hơn bằng cách vào lệnh sớm hơn — chỉ có rủi ro cao hơn.

**Nguyên tắc 2 — Kỷ luật:**
SuperTrend đảo chiều BÁN = Thoát lệnh, không có ngoại lệ. SL bị kích hoạt = Chấp nhận thua lỗ nhỏ, bảo toàn vốn cho cơ hội tiếp theo.

**Nguyên tắc 3 — Quản lý vốn:**
Không có cổ phiếu nào đáng để đặt cược toàn bộ vốn. Luôn tuân thủ quy tắc phân bổ 30% và rủi ro 2%.

**Nguyên tắc 4 — Học liên tục:**
Ghi nhật ký giao dịch. Sau mỗi lệnh (thắng hoặc thua), phân tích lại: Tín hiệu có đủ? Quyết định có đúng? Kết quả có thể cải thiện?

## 20.2. Con Đường Phát Triển Của Nhà Đầu Tư

**Giai đoạn 1 — Học thuộc (Tháng 1-3):**
Hiểu rõ tất cả tính năng. Backtest nhiều cổ phiếu. Chưa giao dịch tiền thật.

**Giai đoạn 2 — Thực hành (Tháng 3-6):**
Giao dịch với vốn nhỏ. Tuân thủ nghiêm ngặt kế hoạch. Ghi nhật ký đầy đủ.

**Giai đoạn 3 — Tối ưu (Tháng 6-12):**
Phân tích nhật ký, tìm điểm mạnh/yếu. Điều chỉnh cài đặt cho phù hợp phong cách cá nhân.

**Giai đoạn 4 — Thành thục (Năm 2 trở đi):**
Hệ thống giao dịch ổn định. Vốn tăng dần. Tập trung vào Siêu Cổ Phiếu.

## 20.3. Lời Kết

ManhDucCapital không phải "máy in tiền" — không có công cụ nào như vậy trên thị trường. Đây là một hệ thống phân tích kỹ thuật toàn diện giúp bạn:

- **Nhìn rõ hơn** xu hướng và sức mạnh của thị trường
- **Hành động có kỷ luật** dựa trên tín hiệu, không phải cảm xúc
- **Quản lý rủi ro** một cách khoa học và nhất quán
- **Tối đa hóa lợi nhuận** bằng cách giữ lệnh đúng cách

Kết quả phụ thuộc vào bạn — kỷ luật thực thi, kiên nhẫn nắm giữ, và không ngừng học hỏi. Chúc bạn giao dịch thành công!

---

*ManhDucCapital | Vũ Mạnh Đức | 0973124824*
*Phiên bản tài liệu: 2026.05 | Pine Script v6*
*Tuyên bố: Chỉ báo chỉ mang tính tham khảo. Quyết định đầu tư thuộc về bạn.*
