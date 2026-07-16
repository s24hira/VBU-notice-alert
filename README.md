# Visva-Bharati Notice Alert Bot 🚀

A modern, lightweight Telegram bot designed to monitor the official Visva-Bharati University website for new notices, extract and summarize PDF contents using Google's **Gemini 3.5 Flash** model, and send instant alerts to subscribed users. It specifically tracks the 10 most recent notices.

---

## 🌟 Key Features

- **Automated Monitoring:** Continuously scans the Visva-Bharati website for new notices and the Samarth portal for examination results at randomized, natural intervals (10–20 minutes).
- **Direct PDF Summarization:** Uses Google's **Gemini REST API** (directly over HTTP to minimize RAM overhead) to summarize PDFs inline and extract target audience parameters.
- **Strict Notice Processing:** Mandates successful summary extraction. If summarization fails or returns empty, the notice is skipped and intelligently deferred for retry.
- **Intelligent Retry Logic:** Integrates an exponential backoff mechanism for the Gemini API to gracefully handle sudden rate-limiting or 503 unavailability errors.
- **Instant Targeted Alerts:** Dispatches notice and result titles, links, and summaries. Users receive notifications perfectly matched to their institute and department configurations!
- **Interactive UI:** Configure your subscription seamlessly using our 3-Step Telegram onboarding flow.
- **Robust Storage:** Powered by **Supabase** (PostgreSQL) for resilient, structured tracking of subscribers and notices.
- **Interactive Verification**: Includes an end-to-end `test_alert.py` testing script to instantly verify the scraper, Gemini API, and Telegram alerts.
- **Memory Optimized Architecture:** Addresses glibc heap fragmentation in Docker via periodic `malloc_trim(0)` calls and tuned `MALLOC_MMAP_THRESHOLD_` / `MALLOC_TRIM_THRESHOLD_` environment variables, ensuring freed memory is returned to the OS. Also recycles the Supabase `httpx` connection pool every ~3 hours and uses stateless HTTP requests with strict context managers throughout.
- **Multi-Strategy Anti-Bot Bypass:** Uses a layered HTTP client (`cloudscraper` → `curl_cffi` → session warmup) with rotated User-Agents, full browser-grade headers, TLS fingerprint impersonation, and cookie pre-warming to reliably bypass WAF/anti-bot protections on the Samarth eGov portal.

---

## 📋 Prerequisites

Before running or deploying the bot, make sure you have:

1. **Python 3.11+** installed (if running directly).
2. **Telegram Bot Token:** Created via [@BotFather](https://t.me/botfather).
3. **Google Gemini API Key:** Generated from [Google AI Studio](https://aistudio.google.com/).
4. **Supabase URL & Key:** Set up a free PostgreSQL project on [Supabase](https://supabase.com/).

---

## 🛠️ Configuration

Create a `.env` file in the root directory and populate it with your credentials:

```dotenv
# Telegram Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Gemini API Configuration
GEMINI_API_KEY=your_gemini_api_key_here

# Supabase Storage Configuration
SUPABASE_URL=your_supabase_project_url_here
SUPABASE_KEY=your_supabase_anon_or_service_role_key_here

# Optional Settings
VBU_WEBSITE_URL=https://www.visvabharati.ac.in/home/all-notices/
SAMARTH_RESULTS_URL=https://visvabharati.samarth.edu.in/index.php/notifications/index
```

---

## 🚀 Local Run (Without Docker)

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Verify Setup (Recommended):**
   Run the end-to-end test script to verify scraping, Gemini summarization, and Telegram notifications:
   ```bash
   python test_alert.py
   ```

3. **Start the Bot:**
   ```bash
   python main.py
   ```

---

## 🐳 Deployment (With Docker & Docker Compose)

1. **Deploy using Docker Compose:**
   ```bash
   docker-compose up -d --build
   ```

2. **View Logs:**
   ```bash
   docker-compose logs -f
   ```

---

## 🤖 Bot Commands

Interact with the bot on Telegram using:

- `/start` - Setup notice alerts via the UI configuration (Institute -> Department -> Name).
- `/settings` - Manage your subscription:
  - **🔄 Reset Subscription** — Re-run the full setup flow (Institute → Department → Name) without clearing existing DB data.
  - **🗑️ Delete Account** — Permanently erase all your subscription data with a confirmation step.
- `/status` - Check current bot status.
- `/ping` - Confirm bot is online (responds with `Pong!`).

---

## 📁 Repository Structure

```
├── bot/
│   ├── utils/
│   │   ├── summarizer.py     # Gemini structured extraction and summary logic
│   │   └── http_client.py    # Multi-strategy resilient HTTP client (anti-bot bypass)
│   ├── constants.py          # Categorization constants (Bhavanas, Depts)
│   ├── handlers.py           # Telegram command and inline callback handlers
│   ├── notice_processor.py   # Scraper, target-filtering, and alert coordinator for notices
│   ├── result_processor.py   # Scraper, target-filtering, and alert coordinator for exam results
│   └── storage.py            # Supabase PostgreSQL database integration
├── data/                     # Local data cache
├── main.py                   # Main bot execution entrypoint
├── test_alert.py             # E2E test verification script
├── test_integration.py       # Mock integration unit tests
├── requirements.txt          # Python dependencies
├── Dockerfile                # Multi-stage lightweight Docker image
└── docker-compose.yml        # Docker Compose configuration
```

---

## ☁️ Deployment on Koyeb

The bot runs as a **Web Service** on Koyeb with a TCP health check on **port 8000**.

1. **Set environment variables** in the Koyeb dashboard (never commit `.env`):
   - `TELEGRAM_BOT_TOKEN`
   - `GEMINI_API_KEY`
   - `SUPABASE_URL`
   - `SUPABASE_KEY`

2. **Health check**: configure Koyeb to check `GET /health` on port `8000`.

3. **Port**: set the service port to `8000`.

---

## 🔒 Security

The codebase has been hardened against the following:

| Area | Mitigation |
|------|-----------|
| Health check server | Uses `BaseHTTPRequestHandler`; only `/` and `/health` return 200, all other paths 404. Static file serving is explicitly prevented. |
| Secrets | All credentials are injected as environment variables. `.env` is in both `.gitignore` and `.dockerignore`. |
| Callback origin | Every Telegram inline callback validates `from_user.id == chat.id` before processing account-level actions. |
| Callback data | All integer values parsed from callback payloads are wrapped in `try/except` with explicit bounds checks for page indices. |
| URL allowlist | Outbound requests are restricted to `visvabharati.ac.in`, `visvabharati.samarth.edu.in`, and the exact S3 bucket `samarth-ac.s3.ap-south-1.amazonaws.com`. |
| TLS | All outbound HTTP requests use `certifi` for certificate verification. `curl_cffi` impersonates Chrome's TLS fingerprint (JA3) for stealth. |
| Memory | In-memory user caches use `TTLCache(maxsize=10_000, ttl=7200)` to prevent unbounded growth. |
| Markdown injection | User-supplied names are sanitized and then escaped before insertion into Markdown messages. |
| Logging | All `except` blocks use `logger.exception()` to capture full stack traces. |