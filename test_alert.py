import os
import logging
import requests
from dotenv import load_dotenv
import telebot

from bot.storage import SupabaseStorage
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

def test_supabase_storage():
    logger.info("--- Testing Supabase Storage ---")
    try:
        storage = SupabaseStorage()
        urls = storage.get_all_notice_urls()
        users = storage.get_all_users()
        logger.info(f"SUCCESS: Successfully fetched from Supabase. Users count: {len(users)}, Notices count: {len(urls)}")
        return storage
    except Exception as e:
        logger.error(f"FAILURE: Supabase Storage check failed: {e}")
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
        pdf_bytes = processor.download_pdf_bytes(test_pdf_url)
        if pdf_bytes:
            logger.info(f"SUCCESS: PDF downloaded in memory ({len(pdf_bytes)} bytes)")
            logger.info("Summarizing test PDF with Gemini...")
            extraction = summarizer.summarize_pdf(pdf_bytes)
            logger.info(f"SUCCESS: Summary generated:\n{extraction.summary}")

            logger.info(f"  Institute: {extraction.target_bhavana}")
            logger.info(f"  Dept: {extraction.target_department}")
            logger.info(f"  General: {extraction.is_general}")
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
    storage = test_supabase_storage()
    
    if storage:
        test_scraping_and_summarizer(storage, gemini_key, vbu_url)

if __name__ == '__main__':
    main()
