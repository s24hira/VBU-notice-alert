import os
import random
import logging
import telebot
from telebot import apihelper
import time
from dotenv import load_dotenv
import threading
import gc
import http.server
import socketserver

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

    def run(self):
        try:
            webhook_url = os.getenv('WEBHOOK_URL')
            if webhook_url:
                # Webhook mode
                logger.info(f"Starting in Webhook mode (URL: {webhook_url})")
                try:
                    self.bot.delete_webhook()
                    time.sleep(0.5)
                    full_webhook_url = webhook_url if webhook_url.endswith('/webhook') else webhook_url.rstrip('/') + '/webhook'
                    self.bot.set_webhook(url=full_webhook_url)
                    logger.info("Webhook set successfully")
                except Exception as e:
                    logger.error(f"Error setting webhook: {e}")
            else:
                # Polling mode
                logger.info("Starting in Polling mode")
                self.reset_webhook()
    
                # Start Telegram bot polling in a separate thread.
                # long_polling_timeout=20 is the standard value recommended by the
                # Telegram Bot API docs.
                polling_thread = threading.Thread(
                    target=self.bot.infinity_polling,
                    kwargs={'timeout': 25, 'long_polling_timeout': 20},
                    name='polling',
                )
                polling_thread.daemon = True
                polling_thread.start()
                logger.info("Bot infinity_polling started in separate thread")

            # Run the initial scrape in its own daemon thread so that the
            # polling thread (and therefore all bot commands) are never blocked.
            initial_check_thread = threading.Thread(
                target=self._run_initial_check,
                name='initial_check',
                daemon=True,
            )
            initial_check_thread.start()
            logger.info("Initial check dispatched to background thread")
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

                # Recycle the Supabase client every 6 cycles (~3 hours)
                # to flush accumulated httpx connection pool / SSL state
                if self._check_count % 6 == 0:
                    logger.info("Recycling Supabase client connection pool")
                    self.storage.reconnect()

                    logger.info("Recycling HTTP client connection pools")
                    reset_sessions()

                # Force glibc to return freed memory to OS
                release_memory()
                logger.info(f"Post-GC RSS: {get_rss_mb()} MB")

        except Exception as e:
            logger.error(f"Error in main loop: {type(e).__name__}")
            raise

def make_handler(bot_instance):
    class WebhookAndHealthCheckHandler(http.server.BaseHTTPRequestHandler):
        """HTTP handler for Koyeb TCP health checks and Telegram Webhooks.
    
        Responds 200 OK to GET / and GET /health.
        Receives Telegram updates on POST /webhook.
        """
        def do_GET(self):
            if self.path in ('/', '/health'):
                self.send_response(200)
                self.send_header('Content-Type', 'text/plain; charset=utf-8')
                self.end_headers()
                self.wfile.write(b'OK')
            else:
                self.send_response(404)
                self.end_headers()
                
        def do_POST(self):
            if self.path == '/webhook':
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length).decode('utf-8')
                
                try:
                    update = telebot.types.Update.de_json(post_data)
                    bot_instance.bot.process_new_updates([update])
                    self.send_response(200)
                    self.end_headers()
                except Exception as e:
                    logger.error(f"Error processing webhook update: {e}")
                    self.send_response(500)
                    self.end_headers()
            else:
                self.send_response(404)
                self.end_headers()
    
        def log_message(self, format, *args):
            # Suppress per-request access logs to keep stdout clean
            pass
            
    return WebhookAndHealthCheckHandler

def start_server(bot_instance, port=8000):
    socketserver.TCPServer.allow_reuse_address = True
    handler_class = make_handler(bot_instance)
    try:
        with socketserver.TCPServer(("", port), handler_class) as httpd:
            logger.info(f"Health check and webhook server listening on port {port}")
            httpd.serve_forever()
    except Exception as e:
        logger.error(f"Failed to start server: {e}")

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
    
    # Start the health/webhook server in a background thread
    server_thread = threading.Thread(target=start_server, args=(bot,), kwargs={'port': 8000}, daemon=True)
    server_thread.start()

    bot.run()

if __name__ == '__main__':
    main()
