import os
import random
import logging
import telebot
from telebot import apihelper
import time
from dotenv import load_dotenv
import threading

# Prevent requests.Session memory bloat by recreating it every 5 minutes
apihelper.SESSION_TIME_TO_LIVE = 5 * 60

# Import modular components
from bot.storage import SupabaseStorage
from bot.handlers import BotHandlers
from bot.notice_processor import NoticeProcessor
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

# Ensure data directory exists
os.makedirs('data', exist_ok=True)
os.makedirs('data/temp', exist_ok=True)

class VBUNoticeBot:
    def __init__(self):
        self.bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
        self.storage = SupabaseStorage()
        self.summarizer = GeminiPDFSummarizer(GEMINI_API_KEY)
        self.notice_processor = NoticeProcessor(self.summarizer, self.storage, VBU_WEBSITE_URL)
        self.handlers = BotHandlers(self.bot, self.storage)

    def reset_webhook(self):
        """Reset any existing webhook to ensure clean polling"""
        try:
            self.bot.delete_webhook()
            time.sleep(0.5)  # Wait for webhook deletion to complete
            logger.info("Webhook reset successful")
        except Exception as e:
            logger.error(f"Error resetting webhook: {e}")

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
                logger.info("Starting initial notice check")
                self.notice_processor.process_new_notices(self.bot)
            except Exception as e:
                logger.error(f"Error in initial notice check: {e}")

            while True:
                next_interval = random.randint(1800, 2400)  # 30-40 minutes
                logger.info(f"Next check scheduled in {next_interval} seconds")
                time.sleep(next_interval)
                
                try:
                    self.notice_processor.process_new_notices(self.bot)
                except Exception as e:
                    logger.error(f"Error in scheduled job: {e}")

        except Exception as e:
            logger.error(f"Error in main loop: {e}")
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