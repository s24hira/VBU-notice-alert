import os
import logging
import requests
import certifi
import datetime
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse

from bot.utils.summarizer import GeminiPDFSummarizer, SummarizationError, NoticeExtraction
from bot.storage import SupabaseStorage

logger = logging.getLogger(__name__)

class NoticeProcessor:
    def __init__(self, summarizer: GeminiPDFSummarizer, storage: SupabaseStorage, website_url: str):
        self.summarizer = summarizer
        self.storage = storage
        self.website_url = website_url
        self.MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
        self.ALLOWED_DOMAINS = [
            'visvabharati.ac.in',
            'visvabharati.samarth.edu.in',
        ]

    def _is_safe_url(self, url):
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return False
            host = parsed.hostname or ''
            # Exact match OR proper subdomain (host ends with '.<domain>')
            # This prevents 'evil-visvabharati.ac.in' from matching 'visvabharati.ac.in'
            return any(
                host == domain or host.endswith('.' + domain)
                for domain in self.ALLOWED_DOMAINS
            )
        except Exception:
            return False

    def scrape_notices(self, existing_titles=None, existing_urls=None, max_retries=3):
        existing_titles = existing_titles or set()
        existing_urls = existing_urls or set()

        for attempt in range(max_retries):
            try:
                with requests.get(self.website_url, timeout=30, verify=certifi.where()) as response:
                    soup = BeautifulSoup(response.content, 'html.parser')

                notice_boxes = soup.find_all('div', {'class': 'an-noticebox'})
                if not notice_boxes:
                    logger.error("Could not find any an-noticebox divs.")
                    continue

                new_notices = []
                logger.info(f"Using {len(existing_urls)} existing notice records for deduplication.")

                for box in notice_boxes[:10]:
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
                            logger.exception(f"Could not parse date: {date_string}")

                    if notice_link not in existing_urls and notice_title not in existing_titles:
                        new_notices.append({
                            'title': notice_title,
                            'link': notice_link,
                            'date': notice_date
                        })

                # Free memory
                del notice_boxes
                del soup

                return new_notices

            except Exception:
                logger.exception(f"Error scraping notices (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(5)
                
        return []

    def download_pdf_bytes(self, pdf_url, max_retries=3):
        if not self._is_safe_url(pdf_url):
            logger.error(f"Unsafe or unauthorized URL requested: {pdf_url}")
            return None

        for attempt in range(max_retries):
            try:
                with requests.get(pdf_url, timeout=30, verify=certifi.where(), stream=True) as response:
                    content_length = int(response.headers.get('content-length', 0))
                    if content_length > self.MAX_PDF_SIZE:
                        logger.error(f"PDF too large: {content_length} bytes")
                        return None

                    if not response.headers.get('content-type', '').startswith('application/pdf'):
                        logger.error("Downloaded file is not a PDF")
                        return None

                    chunks = []
                    downloaded = 0
                    for chunk in response.iter_content(chunk_size=8192):
                        downloaded += len(chunk)
                        if downloaded > self.MAX_PDF_SIZE:
                            logger.error("PDF exceeds size limit during download")
                            return None
                        chunks.append(chunk)

                    content = b''.join(chunks)
                    if len(content) < 100:
                        logger.error("Downloaded PDF file is too small")
                        return None

                    return content

            except Exception:
                logger.exception(f"PDF download error (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    time.sleep(5)

        return None

    def send_telegram_alerts(self, bot, notice, summary_text, user_ids):
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

                if summary_text:
                    summary_message = f"""
✨ AI Summary:

{summary_text}
                    """
                    bot.send_message(user_id, summary_message)

            except Exception:
                logger.exception(f"Telegram message send error to user {user_id}")

    def process_new_notices(self, bot, existing_titles=None, existing_urls=None):
        import gc
        try:
            logger.info("Checking for new notices")
            new_notices = self.scrape_notices(existing_titles, existing_urls)
            logger.info(f"Found {len(new_notices)} new notices")

            for notice in new_notices:
                try:
                    logger.info(f"Processing notice: {notice['title']}")
                    pdf_bytes = self.download_pdf_bytes(notice['link'])
                    if not pdf_bytes:
                        continue

                    logger.info("Generating summary using Gemini")
                    try:
                        extraction = self.summarizer.summarize_pdf(pdf_bytes)
                    except SummarizationError:
                        logger.exception("Summarization failed")
                        logger.warning(f"Strict requirement not met: Skipping notice '{notice['title']}' due to summarization failure.")
                        continue
                    
                    if not extraction or not extraction.summary:
                        logger.error("Strict requirement not met: Extraction yielded empty summary.")
                        logger.warning(f"Skipping notice '{notice['title']}'.")
                        continue

                    # Sleep to respect the Gemini API free tier rate limit (15 requests per minute)
                    time.sleep(5)

                    # Build notice_data for storage and finding users
                    notice_data = {
                        'title': notice['title'],
                        'link': notice['link'],
                        'date': notice['date'],
                        'summary': extraction.summary,
                        'target_bhavana': extraction.target_bhavana,
                        'target_department': extraction.target_department,
                        'is_general': extraction.is_general,
                        'status': 'New'
                    }

                    added_record = self.storage.add_notice(notice_data)

                    if added_record:
                        logger.info("Finding matching subscribers...")
                        matching_users = self.storage.get_matching_subscribers(notice_data)
                        logger.info(f"Sending alerts to {len(matching_users)} matched users")
                        
                        self.send_telegram_alerts(bot, notice, notice_data['summary'], matching_users)
                        # Update status to 'Sent' after successfully sending alerts
                        self.storage.update_notice_status(added_record['id'], 'Sent')
                        logger.info("Notice processed successfully")
                    else:
                        logger.warning(f"Notice '{notice['title']}' was not added to Supabase, skipping alerts.")

                except Exception:
                    logger.exception("Notice processing error")
                finally:
                    # Clear memory for each notice processed
                    if 'pdf_bytes' in locals():
                        del pdf_bytes
                    if 'extraction' in locals():
                        del extraction

        except Exception:
            logger.exception("Error in process_new_notices")
        finally:
            # Force garbage collection
            gc.collect()