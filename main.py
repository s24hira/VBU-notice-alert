import os
import random
import logging
import telebot
from telebot import apihelper
import time
from dotenv import load_dotenv
import threading
import gc
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from concurrent.futures import ThreadPoolExecutor

# Prevent requests.Session memory bloat by recreating it every 5 minutes
apihelper.SESSION_TIME_TO_LIVE = 5 * 60

# --- glibc memory management (Linux/Docker only) ---
# Python frees objects back to glibc's malloc, but glibc keeps the memory
# in internal "arenas" rather than returning it to the OS.  malloc_trim(0)
# forces glibc to scan those arenas and release free pages back to the OS.
try:
    import ctypes
    # SECURITY NOTE: This assumes a trusted container environment.
    # libc.so.6 is loaded for malloc_trim() memory optimization.
    _libc = ctypes.CDLL('libc.so.6')
    _has_malloc_trim = True
except (OSError, AttributeError):
    _has_malloc_trim = False


def release_memory():
    """Force full garbage collection and return freed memory to the OS."""
    gc.collect(generation=2)          # Sweep all three GC generations
    if _has_malloc_trim:
        _libc.malloc_trim(0)          # Release free glibc heap pages to OS


def get_rss_mb():
    """Read current RSS from /proc/self/status (Linux only). Returns -1 on failure."""
    try:
        with open('/proc/self/status') as f:
            for line in f:
                if line.startswith('VmRSS:'):
                    return round(int(line.split()[1]) / 1024, 1)  # KB → MB
    except Exception:
        pass
    return -1


# Import modular components
from bot.storage import SupabaseStorage
from bot.handlers import BotHandlers
from bot.notice_processor import NoticeProcessor
from bot.result_processor import ResultProcessor
from bot.utils.summarizer import GeminiPDFSummarizer
from bot.utils.http_client import reset_sessions

# Configure logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout # Output logs to console
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')
VBU_WEBSITE_URL = os.getenv('VBU_WEBSITE_URL', 'https://www.visvabharati.ac.in/home/all-notices/')
SAMARTH_RESULTS_URL = os.getenv('SAMARTH_RESULTS_URL', 'https://visvabharati.samarth.edu.in/index.php/notifications/index')

# Ensure data directory exists
os.makedirs('data', exist_ok=True)
os.makedirs('data/temp', exist_ok=True)

class VBUNoticeBot:
    def __init__(self):
        self.webhook_secret = None
        self.bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
        self.storage = SupabaseStorage()
        self.summarizer = GeminiPDFSummarizer(GEMINI_API_KEY)
        self.notice_processor = NoticeProcessor(self.summarizer, self.storage, VBU_WEBSITE_URL)
        self.result_processor = ResultProcessor(self.summarizer, self.storage, SAMARTH_RESULTS_URL)
        self.handlers = BotHandlers(self.bot, self.storage)
        self._check_count = 0

    def reset_webhook(self):
        """Reset any existing webhook to ensure clean polling"""
        try:
            self.bot.delete_webhook()
            time.sleep(0.5)  # Wait for webhook deletion to complete
            logger.info("Webhook reset successful")
        except Exception as e:
            logger.error(f"Error resetting webhook: {type(e).__name__}")

    def _run_initial_check(self):
        """Run the first notice/result scrape in a background thread.

        Keeping this off the main thread means the polling thread is never
        starved during startup — bot commands are responsive immediately.
        """
        # Give infinity_polling a couple of seconds to fully establish its
        # long-poll connection before we saturate the network/DB with the
        # initial scrape.
        time.sleep(2)
        try:
            logger.info("Starting initial notice and result check (background thread)")
            existing_titles, existing_urls = self.storage.get_existing_notices()
            self.notice_processor.process_new_notices(self.bot, existing_titles, existing_urls)
            self.result_processor.process_new_results(self.bot, existing_titles, existing_urls)
        except Exception as e:
            logger.error(f"Error in initial check: {type(e).__name__}")
        finally:
            release_memory()
            logger.info(f"Initial RSS after GC: {get_rss_mb()} MB")

    def scheduled_checks_loop(self):
        logger.info("Starting main loop")
        while True:
            next_interval = random.randint(600, 1200)  # 10-20 minutes
            logger.info(f"Next check in {next_interval}s | RSS: {get_rss_mb()} MB")
            time.sleep(next_interval)

            try:
                existing_titles, existing_urls = self.storage.get_existing_notices()
                self.notice_processor.process_new_notices(self.bot, existing_titles, existing_urls)
                self.result_processor.process_new_results(self.bot, existing_titles, existing_urls)
            except Exception as e:
                logger.error(f"Error in scheduled job: {type(e).__name__}")

            self._check_count += 1

            # Aggressively recycle HTTP clients and Supabase connections every cycle
            # to ensure zero idle connections and minimize idle memory bloat
            logger.info("Recycling Supabase client connection pool")
            self.storage.reconnect()

            logger.info("Recycling HTTP client connection pools")
            reset_sessions()

            # Force glibc to return freed memory to OS
            release_memory()
            logger.info(f"Post-GC RSS: {get_rss_mb()} MB")

# Global variables for FastAPI lifecycle
bot_instance = None
executor = ThreadPoolExecutor(max_workers=2)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot_instance
    logger.info("Starting Visva-Bharati Notice Bot application...")
    
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable not set")
        return
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY environment variable not set")
        return
    if not os.getenv('SUPABASE_URL') or not os.getenv('SUPABASE_KEY'):
        logger.error("SUPABASE_URL or SUPABASE_KEY not set in environment variables.")
        return
        
    bot_instance = VBUNoticeBot()

    webhook_url = os.getenv('WEBHOOK_URL')
    if webhook_url:
        # Webhook mode
        import secrets
        bot_instance.webhook_secret = secrets.token_urlsafe(32)
        logger.info(f"Starting in Webhook mode (URL: {webhook_url})")
        
        def _setup_webhook():
            try:
                bot_instance.bot.delete_webhook()
                time.sleep(0.5)
                full_webhook_url = webhook_url if webhook_url.endswith('/webhook') else webhook_url.rstrip('/') + '/webhook'
                bot_instance.bot.set_webhook(url=full_webhook_url, secret_token=bot_instance.webhook_secret)
                logger.info("Webhook set securely with secret token")
            except Exception as e:
                logger.error(f"Error setting webhook: {e}")
                
        # Run synchronous webhook setup in a background thread
        threading.Thread(target=_setup_webhook, daemon=True).start()
    else:
        # Polling mode
        logger.info("Starting in Polling mode")
        bot_instance.reset_webhook()

        # Start Telegram bot polling in a separate thread.
        polling_thread = threading.Thread(
            target=bot_instance.bot.infinity_polling,
            kwargs={'timeout': 60, 'long_polling_timeout': 20},
            name='polling',
        )
        polling_thread.daemon = True
        polling_thread.start()
        logger.info("Bot infinity_polling started in separate thread")

    # Run the initial scrape in its own daemon thread so that the
    # polling thread (and therefore all bot commands) are never blocked.
    initial_check_thread = threading.Thread(
        target=bot_instance._run_initial_check,
        name='initial_check',
        daemon=True,
    )
    initial_check_thread.start()
    logger.info("Initial check dispatched to background thread")

    # Start the scheduled background loop
    loop_thread = threading.Thread(target=bot_instance.scheduled_checks_loop, daemon=True)
    loop_thread.start()
    logger.info("Scheduled checks loop dispatched to background thread")

    yield # Let FastAPI handle web requests here

    # Cleanup
    if executor:
        executor.shutdown(wait=False)

app = FastAPI(lifespan=lifespan)

@app.get("/")
@app.get("/health")
async def health_check():
    return "OK"

@app.post("/webhook")
async def handle_webhook(request: Request):
    if not bot_instance:
        raise HTTPException(status_code=503, detail="Bot is not initialized yet")

    # Validate Telegram's secret token to prevent spoofing
    secret_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
    if bot_instance.webhook_secret and secret_token != bot_instance.webhook_secret:
        logger.warning("Unauthorized webhook access attempt (Invalid secret token)")
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        post_data = await request.body()
        post_data_str = post_data.decode('utf-8')
        update = telebot.types.Update.de_json(post_data_str)
        
        # 3. Offload CPU-Heavy Tasks (Critical)
        # Using a ThreadPoolExecutor prevents the entire async event loop 
        # from freezing while pyTelegramBotAPI synchronously processes the update.
        loop = asyncio.get_event_loop()
        loop.run_in_executor(
            executor, 
            bot_instance.bot.process_new_updates, 
            [update]
        )
        
        return "OK"
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

if __name__ == '__main__':
    port = int(os.getenv("PORT", 8000))
    # Override WEB_CONCURRENCY to prevent PaaS providers (like Koyeb) from 
    # starting multiple uvicorn workers, which duplicates background threads.
    if "WEB_CONCURRENCY" in os.environ:
        del os.environ["WEB_CONCURRENCY"]
    logger.info(f"Starting uvicorn server on port {port}")
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info", limit_concurrency=20, workers=1)
