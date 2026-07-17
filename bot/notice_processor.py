import os
import logging
import requests
import certifi
import datetime
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse

from bot.utils.http_client import resilient_get, resilient_download_file

from bot.utils.summarizer import GeminiPDFSummarizer, SummarizationError, NoticeExtraction
from bot.storage import SupabaseStorage

logger = logging.getLogger(__name__)

class NoticeProcessor:
    def __init__(self, summarizer: GeminiPDFSummarizer, storage: SupabaseStorage, website_url: str):
        self.summarizer = summarizer
        self.storage = storage
        self.website_url = website_url
        self.MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
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

        try:
            response = resilient_get(self.website_url, timeout=30,
                                     max_retries=max_retries)
            soup = BeautifulSoup(response.content, 'html.parser')

            notice_boxes = soup.find_all('div', {'class': 'an-noticebox'})
            if not notice_boxes:
                page_title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
                logger.error(f"Could not find any an-noticebox divs. Status: {response.status_code} | Page Title: {page_title}")
                return []

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
            logger.exception("Error scraping notices after all bypass strategies")
                
        return []

    def download_file(self, file_url, max_retries=3):
        if not self._is_safe_url(file_url):
            logger.error(f"Unsafe or unauthorized URL requested: {file_url}")
            return None, None

        try:
            response = resilient_download_file(file_url, timeout=30,
                                         max_retries=max_retries)

            content_length = int(response.headers.get('content-length', 0))
            if content_length > self.MAX_FILE_SIZE:
                logger.error(f"File too large: {content_length} bytes")
                return None, None

            content = response.content

            content_type = response.headers.get('content-type', '').lower()
            
            mime_type = None
            if content_type.startswith('application/pdf') or b'%PDF-' in content[:50]:
                mime_type = 'application/pdf'
            elif content_type.startswith('image/'):
                mime_type = content_type.split(';')[0]
            else:
                if content.startswith(b'\xff\xd8\xff'):
                    mime_type = 'image/jpeg'
                elif content.startswith(b'\x89PNG\r\n\x1a\n'):
                    mime_type = 'image/png'

            if not mime_type:
                preview = content[:50].decode('utf-8', errors='ignore').replace('\n', ' ')
                logger.error(f"Downloaded file is not a supported PDF or Image. Content-Type: {content_type} | Preview: {preview}")
                return None, None

            if len(content) > self.MAX_FILE_SIZE:
                logger.error("File exceeds size limit")
                return None, None

            if len(content) < 100:
                logger.error("Downloaded file is too small")
                return None, None

            return content, mime_type

        except Exception:
            logger.exception(f"File download failed after all strategies")

        return None, None

    def send_telegram_alerts(self, bot, notice, summary_text, user_ids):
        for user_id in user_ids:
            try:
                date_str = notice['date'].strftime('%b %d, %Y') if isinstance(notice.get('date'), datetime.date) else "N/A"
                
                safe_title = notice['title']
                if len(safe_title) > 2000:
                    safe_title = safe_title[:2000] + "..."

                alert_message = f"""
🚨New Visva-Bharati Notice!🚨

Title: {safe_title}

Date: {date_str}

Link: {notice['link']}
                """
                bot.send_message(user_id, alert_message)

                if summary_text:
                    safe_summary = summary_text
                    if len(safe_summary) > 3900:
                        safe_summary = safe_summary[:3900] + "..."
                        
                    summary_message = f"""
✨ AI Summary:

{safe_summary}
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
                    file_bytes, mime_type = self.download_file(notice['link'])
                    
                    if not file_bytes or not mime_type:
                        logger.info(f"File unavailable for '{notice['title']}'. Falling back to text categorization.")
                        try:
                            extraction = self.summarizer.categorize_text(notice['title'])
                        except SummarizationError:
                            logger.exception("Text categorization failed")
                            logger.warning(f"Strict requirement not met: Skipping notice '{notice['title']}' due to text categorization failure.")
                            continue
                    else:
                        logger.info(f"Generating summary using Gemini (MIME: {mime_type})")
                        try:
                            extraction = self.summarizer.summarize_document(file_bytes, mime_type=mime_type)
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
                    if 'file_bytes' in locals():
                        del file_bytes
                    if 'extraction' in locals():
                        del extraction

        except Exception:
            logger.exception("Error in process_new_notices")
        finally:
            # Force garbage collection
            gc.collect()