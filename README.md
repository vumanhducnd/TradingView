# ManhDucCapital Scanner

SuperTrend + BiasNorm scanner cho Top 100 VN-Index.

## Cấu trúc

```
scanner/        # Logic chính: fetch, indicators, scan, backtest
dashboard/      # Streamlit dashboard
data/           # CSV snapshot (signals, backtest)
sql/            # Schema PostgreSQL
```

## Cài đặt

```bash
pip install -r requirements.txt
```

Copy `.env` và điền thông tin:

```bash
cp .env.example .env
```

## Database

### Local (Docker)

```bash
docker-compose up -d
```

### Cloud (Neon — free)

Đăng ký tại [neon.tech](https://neon.tech), tạo project, copy connection string vào `.env`:

```env
DATABASE_URL=postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require
```

Khởi tạo schema:

```bash
python -c "
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
conn.cursor().execute(open('sql/init.sql').read())
conn.commit()
"
```

### Backup local → Neon

```bash
# 1. Dump từ Docker local
docker exec trading_db pg_dump -U trading_user -d trading --no-owner --no-acl --clean --if-exists -f /tmp/backup.sql
docker cp trading_db:/tmp/backup.sql ./backup.sql

# 2. Restore lên Neon
docker run --rm -v "${PWD}:/backup" postgres:16-alpine \
  psql "postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require" \
  -f /backup/backup.sql

# 3. Xóa file tạm
rm backup.sql
```

## Chạy

### Load watchlist vào DB

```bash
python -m scanner.crawler --init-watchlist
```

### Crawl lịch sử OHLCV (1 lần đầu)

```bash
python -m scanner.crawler
```

### Chạy scanner hàng ngày

```bash
python -m scanner.updater
```

### Load CSV local lên DB

```bash
python load_local_to_db.py
```

### Dashboard

```bash
streamlit run dashboard/app.py
```

## GitHub Actions

Scanner tự động chạy sau giờ đóng cửa thị trường (3:30 PM ICT).  
Kết quả gửi qua Telegram và lưu vào DB.
