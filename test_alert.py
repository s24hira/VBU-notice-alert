import os
import logging
import requests
from dotenv import load_dotenv
import telebot

from bot.storage import JsonbinStorage
from bot.notice_processor import NoticeProcessor
from bot.utils.summarizer import GeminiPDFSummarizer

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("TestAlert")

def test_telegram_bot(token):
    logger.info("--- Testing Telegram Bot API Token ---")
    if not token:
        logger.error("FAILURE: TELEGRAM_BOT_TOKEN environment variable not set.")
        return None
    try:
        bot = telebot.TeleBot(token)
        me = bot.get_me()
        logger.info(f"SUCCESS: Bot connection verified. Username: @{me.username}, ID: {me.id}")
        return bot
    except Exception as e:
        logger.error(f"FAILURE: Telegram Bot verification failed: {e}")
        return None

def test_jsonbin_storage():
    logger.info("--- Testing JSONBin Storage ---")
    try:
        storage = JsonbinStorage()
        data = storage._fetch_data()
        logger.info(f"SUCCESS: Successfully fetched from JSONBin. Users count: {len(data.get('users', []))}, Notices count: {len(data.get('notices', []))}")
        return storage
    except Exception as e:
        logger.error(f"FAILURE: JSONBin Storage check failed: {e}")
        return None

def test_scraping_and_summarizer(storage, gemini_key, vbu_url):
    logger.info("--- Testing Scraping and Gemini Summarizer ---")
    if not gemini_key:
        logger.error("FAILURE: GEMINI_API_KEY environment variable not set.")
        return
    try:
        summarizer = GeminiPDFSummarizer(gemini_key)
        processor = NoticeProcessor(summarizer, storage, vbu_url)
        
        logger.info(f"Scraping notices from: {vbu_url} ...")
        notices = processor.scrape_notices()
        logger.info(f"SUCCESS: Scraped {len(notices)} new notices.")
        for idx, notice in enumerate(notices[:3]):
            logger.info(f"  [{idx+1}] Title: {notice['title']}")
            logger.info(f"      Link: {notice['link']}")
            logger.info(f"      Date: {notice['date']}")
        
        # Test PDF download and summarization using a standard sample PDF
        test_pdf_url = "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
        logger.info(f"Downloading test PDF from: {test_pdf_url} ...")
        pdf_path = processor.download_pdf(test_pdf_url)
        if pdf_path:
            logger.info(f"SUCCESS: PDF downloaded to: {pdf_path}")
            logger.info("Summarizing test PDF with Gemini...")
            summary = summarizer.summarize_pdf(pdf_path)
            logger.info(f"SUCCESS: Summary generated:\n{summary}")
            
            try:
                if os.path.exists(pdf_path):
                    os.remove(pdf_path)
                    logger.info("Cleaned up temporary test PDF.")
            except Exception as ex:
                logger.warning(f"Failed to remove test PDF file: {ex}")
        else:
            logger.error("FAILURE: PDF download failed.")
    except Exception as e:
        logger.error(f"FAILURE: Scraping/Summarizer test encountered error: {e}")

def main():
    load_dotenv()
    
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    gemini_key = os.getenv('GEMINI_API_KEY')
    vbu_url = os.getenv('VBU_WEBSITE_URL', 'https://www.visvabharati.ac.in/home/all-notices/')
    
    bot = test_telegram_bot(token)
    storage = test_jsonbin_storage()
    
    if storage:
        test_scraping_and_summarizer(storage, gemini_key, vbu_url)

if __name__ == '__main__':
    main()
