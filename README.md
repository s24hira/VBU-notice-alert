# Visva-Bharati Notice Alert Bot 🚀

A modern, lightweight Telegram bot designed to monitor the official Visva-Bharati University website for new notices, extract and summarize PDF contents using Google's **Gemini 3.5 Flash** model, and send instant alerts to subscribed users. It specifically tracks the 10 most recent notices.

---

## 🌟 Key Features

- **Automated Monitoring:** Continuously scans the Visva-Bharati website for new notices at randomized, natural intervals (5–8 minutes).
- **Direct PDF Summarization:** Uses Google's modern **Gemini GenAI Client** to summarize PDFs inline and extract target audience parameters.
- **Instant Targeted Alerts:** Dispatches notice titles, links, and summaries. Users receive notifications perfectly matched to their academic level, institute, and department configurations!
- **Interactive UI:** Configure your subscription seamlessly using our 3-Step Telegram Inline Keyboard flow.
- **Robust Storage:** Powered by **Supabase** (PostgreSQL) for resilient, structured tracking of subscribers and notices.
- **Interactive Verification**: Includes an end-to-end `test_alert.py` testing script to instantly verify the scraper, Gemini API, and Telegram alerts.

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

- `/start` - Setup notice alerts via the 3-step UI configuration (Level -> Institute -> Department).
- `/settings` - Reconfigure your subscription perfectly matching your needs.
- `/status` - Check current bot status.
- `/ping` - Confirm bot is online (responds with `Pong!`).
- `/help` - View a list of all available commands.

---

## 📁 Repository Structure

```
├── bot/
│   ├── utils/
│   │   └── summarizer.py     # Gemini structured extraction and summary logic
│   ├── constants.py          # Categorization constants (Levels, Bhavanas, Depts)
│   ├── handlers.py           # Telegram command and inline callback handlers
│   ├── notice_processor.py   # Scraper, target-filtering, and alert coordinator
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

## 🤝 Contributing

Contributions are welcome! Feel free to fork the repository, make improvements, and submit pull requests. For bugs or feature requests, open an issue in the repository.

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
