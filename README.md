# Visva-Bharati Notice Alert Bot 🚀

A modern, lightweight Telegram bot designed to monitor the official Visva-Bharati University website for new notices, extract and summarize PDF contents using Google's **Gemini 3.5 Flash** model, and send instant alerts to subscribed users. It specifically tracks the 20 most recent notices.

---

## 🌟 Key Features

- **Automated Monitoring:** Continuously scans the Visva-Bharati website for new notices at randomized, natural intervals (5–8 minutes).
- **Direct PDF Summarization:** Uses Google's modern **Gemini 2.0/3.5 GenAI Client** to summarize PDFs inline without slow, bulky PDF-to-image conversions.
- **Instant Alerts:** Dispatches notice titles, direct links, and clear bullet-point summaries to all subscribed Telegram users.
- **Lightweight Storage:** Migrated to **JSONBin.io** for serverless, configuration-free storage of notices and subscriber lists.
- **Interactive Verification**: Includes an end-to-end `test_alert.py` testing script to instantly verify the scraper, Gemini API, and Telegram alerts.

---

## 📋 Prerequisites

Before running or deploying the bot, make sure you have:

1. **Python 3.11+** installed (if running directly).
2. **Telegram Bot Token:** Created via [@BotFather](https://t.me/botfather).
3. **Google Gemini API Key:** Generated from [Google AI Studio](https://aistudio.google.com/).
4. **JSONBin API Key & Bin ID:** Set up a free account and a bin on [JSONBin.io](https://jsonbin.io/).

---

## 🛠️ Configuration

Create a `.env` file in the root directory and populate it with your credentials:

```dotenv
# Telegram Configuration
TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

# Gemini API Configuration
GEMINI_API_KEY=your_gemini_api_key_here

# JSONBin Storage Configuration
JSONBIN_API_KEY=your_jsonbin_api_key_here
JSONBIN_BIN_ID=your_jsonbin_bin_id_here

# Optional Settings
NEET_WEBSITE_URL=https://www.visvabharati.ac.in/home/all-notices/
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

- `/start` - Subscribe to receive alerts for new Visva-Bharati notices.
- `/status` - Check if you are currently subscribed.
- `/ping` - Confirm bot is online (responds with `Pong!`).
- `/help` - View a list of all available commands.

---

## 📁 Repository Structure

```
├── bot/
│   ├── utils/
│   │   └── summarizer.py     # Gemini 3.5 Flash inline PDF summarizer
│   ├── handlers.py           # Telegram command handlers (/start, /ping, etc.)
│   ├── notice_processor.py   # Scraper, PDF downloader, and alert coordinator
│   └── storage.py            # JSONBin.io database integration
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
