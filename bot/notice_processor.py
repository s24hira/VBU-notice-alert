import os
import logging
import requests
import datetime
from bs4 import BeautifulSoup
import time

from bot.utils.summarizer import GeminiPDFSummarizer, SummarizationError
from bot.storage import JsonbinStorage

logger = logging.getLogger(__name__)

class NoticeProcessor:
    def __init__(self, summarizer: GeminiPDFSummarizer, storage: JsonbinStorage, website_url: str):
        self.summarizer = summarizer
        self.storage = storage
        self.website_url = website_url
        self.session = requests.Session()

    def scrape_notices(self, max_retries=3):
        for attempt in range(max_retries):
            try:
                response = self.session.get(self.website_url, timeout=30)
                soup = BeautifulSoup(response.content, 'html.parser')

                notice_boxes = soup.find_all('div', {'class': 'an-noticebox'})
                if not notice_boxes:
                    logger.error("Could not find any an-noticebox divs.")
                    continue

                new_notices = []
                existing_notice_urls = self.storage.get_all_notice_urls()
                logger.info(f"Fetched {len(existing_notice_urls)} existing notice URLs.")

                for box in notice_boxes[:20]:
                    notice_text_div = box.find('div', {'class': 'NoticeText'})
                    if not notice_text_div:
                        continue
                    anchor = notice_text_div.find('a')
                    if not anchor:
                        continue
                        
                    notice_title = anchor.text.strip()
                    notice_link = anchor['href'].strip()
                    
                    date_div = box.find('div', {'class': 'noticeDate'})
                    notice_date = None
                    if date_div:
                        date_string = ' '.join(date_div.text.split())
                        try:
                            notice_date = datetime.datetime.strptime(date_string, '%b %d %Y')
                        except ValueError:
                            logger.error(f"Could not parse date: {date_string}")

                    if notice_link not in existing_notice_urls:
                        new_notices.append({
                            'title': notice_title,
                            'link': notice_link,
                            'date': notice_date
                        })

                return new_notices

            except Exception as e:
                logger.error(f"Error scraping notices (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                
        return []

    def download_pdf_bytes(self, pdf_url, max_retries=3):
        for attempt in range(max_retries):
            try:
                response = self.session.get(pdf_url, timeout=30)
                if not response.headers.get('content-type', '').startswith('application/pdf'):
                    logger.error("Downloaded file is not a PDF")
                    return None

                if len(response.content) < 100:
                    logger.error("Downloaded PDF file is too small")
                    return None

                return response.content

            except Exception as e:
                logger.error(f"PDF download error (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)

        return None

    def send_telegram_alerts(self, bot, notice, summary, user_ids):
        for user_id in user_ids:
            try:
                date_str = notice['date'].strftime('%b %d, %Y') if isinstance(notice.get('date'), datetime.date) else "N/A"
                alert_message = f"""
🚨New Visva-Bharati Notice!🚨

Title: {notice['title']}

Date: {date_str}

PDF Link: {notice['link']}
                """
                bot.send_message(user_id, alert_message)

                if summary:
                    summary_message = f"""
📋 Notice Summary:

{summary}
                    """
                    bot.send_message(user_id, summary_message)

            except Exception as e:
                logger.error(f"Telegram message send error to user {user_id}: {e}")

    def process_new_notices(self, bot):
        try:
            logger.info("Checking for new notices")
            new_notices = self.scrape_notices()
            logger.info(f"Found {len(new_notices)} new notices")

            all_users = self.storage.get_all_users()
            logger.info(f"Fetched {len(all_users)} users.")

            for notice in new_notices:
                summary = None  # Initialize summary to None
                try:
                    logger.info(f"Processing notice: {notice['title']}")
                    pdf_bytes = self.download_pdf_bytes(notice['link'])
                    if not pdf_bytes:
                        continue

                    logger.info("Generating summary using Gemini")
                    try:
                        summary = self.summarizer.summarize_pdf(pdf_bytes)
                    except SummarizationError as e:
                        logger.error(f"Summarization failed: {e}")
                        summary = "Could not generate a summary for this notice. Please check the PDF directly."

                    added_record = self.storage.add_notice({
                        'title': notice['title'],
                        'link': notice['link'],
                        'date': notice['date'],
                        'summary': summary if summary else "Summary not available.",
                        'status': 'New'
                    })

                    if added_record:
                        logger.info("Sending alerts to users")
                        self.send_telegram_alerts(bot, notice, summary, all_users)
                        # Update status to 'Sent' after successfully sending alerts
                        self.storage.update_notice_status(added_record['id'], 'Sent')
                        logger.info("Notice processed successfully")
                    else:
                        logger.warning(f"Notice '{notice['title']}' was not added to Supabase, skipping alerts.")

                except Exception as e:
                    logger.error(f"Notice processing error: {e}")

        except Exception as e:
            logger.error(f"Error in process_new_notices: {e}")