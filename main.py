import os
import random
import logging
import telebot
import schedule
import time
from dotenv import load_dotenv
import threading

# Import modular components
from bot.storage import JsonbinStorage
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
        self.storage = JsonbinStorage()
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
        def scheduled_job():
            try:
                self.notice_processor.process_new_notices(self.bot)
            except Exception as e:
                logger.error(f"Error in scheduled job: {e}")
            
            next_interval = random.randint(1800, 2400)  # 30-40 minutes
            schedule.clear('notice_check')
            schedule.every(next_interval).seconds.do(scheduled_job).tag('notice_check')
            logger.info(f"Next check scheduled in {next_interval} seconds")

        try:
            self.reset_webhook()
            
            logger.info("Starting initial notice check")
            scheduled_job()

            # Start Telegram bot polling in a separate thread
            polling_thread = threading.Thread(target=self.bot.polling, kwargs={'none_stop': True, 'timeout': 30, 'long_polling_timeout': 90})
            polling_thread.daemon = True
            polling_thread.start()
            logger.info("Bot polling started in separate thread")
            logger.info("Starting main scheduler loop")
            while True:
                schedule.run_pending()
                time.sleep(1)

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
    if not os.getenv('JSONBIN_API_KEY') or not os.getenv('JSONBIN_BIN_ID'):
        logger.error("JSONBin API Key or Bin ID not set in environment variables.")
        return
        
    bot = VBUNoticeBot()
    bot.run()

if __name__ == '__main__':
    main()