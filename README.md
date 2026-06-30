# SAMCO Market Pulse - Crawler Engine

Python crawler engine that scrapes NSE & BSE announcements, processes them with AI, and stores results.

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- MinIO server running
- MySQL database running
- Google Gemini API key
- Playwright browsers

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright browsers
python3 -m playwright install chrome chromium

# Copy environment template
cp .env.example .env

# Update .env with your credentials

# Run the crawler
python3 main.py
```

The crawler will run continuously, crawling every 5 minutes (configurable).

## 📁 Project Structure

```
.
├── main.py                # Entry point - runs crawler loop
├── crawler_engine.py      # NSE & BSE scraping logic
├── stealth_browser.py     # Anti-detection browser setup
├── ai_processor.py        # Gemini AI integration
├── minio_client.py        # MinIO storage client
├── db.py                  # MySQL database operations
├── slack_notifier.py      # Slack notifications
└── requirements.txt       # Python dependencies
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file:

```env
# Google Gemini API (required)
GEMINI_API_KEY=your_gemini_api_key_here

# MinIO Configuration
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_SECURE=false
MINIO_BUCKET_PDFS=announcements-pdfs
MINIO_BUCKET_RESULTS=announcements-results

# MySQL Configuration
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=rootpassword
MYSQL_DATABASE=announcements_db

# Crawler Settings
CRAWL_INTERVAL_SECONDS=300

# Slack Notifications (optional)
SLACK_WEBHOOK_URL=
```

## 🗄️ Database Setup

```bash
# Start MySQL with Docker
docker run -d -p 3306:3306 \
  --name mysql-announcements \
  -e MYSQL_ROOT_PASSWORD=rootpassword \
  -e MYSQL_DATABASE=announcements_db \
  mysql:8.0

# Apply schema
mysql -h 127.0.0.1 -u root -prootpassword announcements_db < schema.sql
```

## 📦 MinIO Setup

```bash
# Start MinIO with Docker
docker run -d -p 9000:9000 -p 9001:9001 \
  --name minio-announcements \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin \
  minio/minio server /data --console-address ":9001"
```

## 🤖 How It Works

### Crawl Cycle (every 5 minutes)

1. **Scrape NSE & BSE** - Using Playwright with stealth mode
2. **Download PDFs** - Extract announcement PDFs
3. **AI Processing** - Gemini analyzes each PDF:
   - Summary
   - Sentiment (Positive/Neutral/Negative)
   - Relevance (High/Medium/Low)
   - Key points
4. **Store Results**:
   - PDFs → MinIO bucket `announcements-pdfs`
   - JSON results → MinIO bucket `announcements-results`
   - Metadata → MySQL database
5. **Notify** - Optional Slack notification

### Anti-Detection Features

NSE uses Akamai Bot Manager. The engine bypasses it via:
- Chrome's new headless mode
- `playwright-stealth` patches
- User-Agent rotation
- Random human-like delays
- Cookie persistence
- Mouse movements

## 🐳 Docker Deployment

```bash
# Build image
docker build -t samco-engine .

# Run container
docker run -d \
  --env-file .env \
  samco-engine
```

## 📊 Data Flow

```
NSE/BSE Websites
    ↓ (scrape)
Crawler Engine
    ↓ (AI process)
Google Gemini
    ↓ (store)
MinIO + MySQL
    ↓ (read)
Backend API
```

## 🔗 Integration

### With Backend
Engine writes to MinIO/MySQL, backend reads from them. No direct API calls.

### Storage Format

**MinIO - PDFs:**
```
announcements-pdfs/
  └── {sha256_hash}.pdf
```

**MinIO - Results:**
```
announcements-results/
  └── {announcement_id}.json
```

**MySQL - Metadata:**
```sql
announcements (
  id, company, symbol, exchange, 
  announcement_type, date, crawled_at,
  pdf_url, sha256, summary, sentiment,
  sentiment_score, relevance, key_points
)
```

## 🧪 Testing

```bash
# Run a single crawl cycle
python3 main.py

# Check logs
tail -f crawler.log

# Verify MinIO storage
# Visit http://localhost:9001

# Verify MySQL data
mysql -h 127.0.0.1 -u root -prootpassword announcements_db \
  -e "SELECT COUNT(*) FROM announcements;"
```

## 📝 Development

```bash
# Install dependencies
pip install -r requirements.txt
python3 -m playwright install chrome chromium

# Run crawler
python3 main.py

# Modify crawl interval in .env
CRAWL_INTERVAL_SECONDS=60  # 1 minute for testing
```

## 🎯 Tech Stack

- Python 3.12
- Playwright (browser automation)
- playwright-stealth (anti-detection)
- Google Gemini AI
- MinIO (S3-compatible storage)
- MySQL (database)
- httpx (HTTP client)

## ⚙️ Configuration Options

| Variable | Default | Description |
|----------|---------|-------------|
| `CRAWL_INTERVAL_SECONDS` | 300 | Seconds between crawl cycles |
| `GEMINI_API_KEY` | - | Google Gemini API key (required) |
| `MINIO_ENDPOINT` | localhost:9000 | MinIO server address |
| `MYSQL_HOST` | localhost | MySQL server address |
| `SLACK_WEBHOOK_URL` | - | Slack notifications (optional) |

## 📄 License

Proprietary - SAMCO Securities
