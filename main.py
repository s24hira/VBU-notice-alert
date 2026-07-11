import os
import random
import logging
import telebot
from telebot import apihelper
import time
from dotenv import load_dotenv
import threading
import gc

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

    def run(self):
        try:
            self.reset_webhook()
            
            # Start Telegram bot polling in a separate thread
            polling_thread = threading.Thread(target=self.bot.infinity_polling, kwargs={'timeout': 30, 'long_polling_timeout': 90})
            polling_thread.daemon = True
            polling_thread.start()
            logger.info("Bot infinity_polling started in separate thread")
            logger.info("Starting main loop")
            
            # Initial run
            try:
                logger.info("Starting initial notice and result check")
                existing_titles, existing_urls = self.storage.get_existing_notices()
                self.notice_processor.process_new_notices(self.bot, existing_titles, existing_urls)
                self.result_processor.process_new_results(self.bot, existing_titles, existing_urls)
            except Exception as e:
                logger.error(f"Error in initial check: {type(e).__name__}")

            release_memory()
            logger.info(f"Initial RSS after GC: {get_rss_mb()} MB")

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

                # Recycle the Supabase client every 6 cycles (~3 hours)
                # to flush accumulated httpx connection pool / SSL state
                if self._check_count % 6 == 0:
                    logger.info("Recycling Supabase client connection pool")
                    self.storage.reconnect()

                # Force glibc to return freed memory to OS
                release_memory()
                logger.info(f"Post-GC RSS: {get_rss_mb()} MB")

        except Exception as e:
            logger.error(f"Error in main loop: {type(e).__name__}")
            raise

def main():
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
        
    bot = VBUNoticeBot()
    bot.run()

if __name__ == '__main__':
    main()
