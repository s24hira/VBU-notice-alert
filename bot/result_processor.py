import os
import logging
import requests
import datetime
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse
import certifi

from bot.utils.http_client import resilient_get, resilient_download_file

from bot.utils.summarizer import GeminiPDFSummarizer, SummarizationError, NoticeExtraction
from bot.storage import SupabaseStorage

logger = logging.getLogger(__name__)

class ResultProcessor:
    def __init__(self, summarizer: GeminiPDFSummarizer, storage: SupabaseStorage, website_url: str):
        self.summarizer = summarizer
        self.storage = storage
        self.website_url = website_url
        self.MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
        self.ALLOWED_DOMAINS = [
            'visvabharati.ac.in',
            'visvabharati.samarth.edu.in',
            # Pinned to the exact S3 bucket hostname used by Samarth.
            # The signed query params (X-Amz-*) are dynamic but the hostname is stable.
            'samarth-ac.s3.ap-south-1.amazonaws.com',
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
        # NOTE: dead `import certifi` line removed — certifi is imported at module level

    def scrape_results(self, existing_titles=None, existing_urls=None, max_retries=3):
        existing_titles = existing_titles or set()
        existing_urls = existing_urls or set()

        try:
            response = resilient_get(self.website_url, timeout=30,
                                     max_retries=max_retries)
            soup = BeautifulSoup(response.content, 'html.parser')

            tables = soup.find_all('table')
            if not tables:
                page_title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
                logger.error(f"Could not find any tables on Samarth result page. Status: {response.status_code} | Page Title: {page_title}")
                return []
            
            tbody = tables[0].find('tbody')
            if not tbody:
                logger.error("Could not find tbody in Samarth table.")
                return []

            rows = tbody.find_all('tr', attrs={'data-key': True})
            if not rows:
                logger.error("Could not find any data rows in Samarth table.")
                return []

            new_results = []
            logger.info(f"Using {len(existing_urls)} existing records from storage for deduplication.")

            for row in rows[:10]:
                cells = row.find_all('td')
                if len(cells) < 3:
                    continue
                
                title = cells[0].text.strip()
                
                date_string = cells[1].text.strip()
                notice_date = None
                try:
                    # Samarth date format example: 22 Jun 2026 00:05:35 AM
                    # Python's %I expects 01-12, so if they use 00: for AM we fix it
                    fixed_date_string = date_string.replace(' 00:', ' 12:')
                    notice_date = datetime.datetime.strptime(fixed_date_string, '%d %b %Y %I:%M:%S %p')
                except ValueError:
                    try:
                        # Fallback: Just parse the first 11 chars (e.g. "22 Jun 2026")
                        notice_date = datetime.datetime.strptime(date_string[:11], '%d %b %Y')
                    except ValueError:
                        logger.exception(f"Could not parse date: {date_string}")

                anchor = cells[2].find('a')
                if not anchor or 'href' not in anchor.attrs:
                    continue
                
                pdf_link = anchor['href'].strip()

                if pdf_link not in existing_urls and title not in existing_titles:
                    new_results.append({
                        'title': title,
                        'link': pdf_link,
                        'date': notice_date
                    })

            # Free memory
            del rows
            del tbody
            del tables
            del soup

            return new_results

        except Exception:
            logger.exception("Error scraping results after all bypass strategies")
                
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

            # curl_cffi responses don't support iter_content, so read all at once
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
                logger.error("Downloaded file does not appear to be a supported PDF or Image")
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

    def send_telegram_alerts(self, bot, result, summary_text, user_ids):
        for user_id in user_ids:
            try:
                date_str = result['date'].strftime('%b %d, %Y') if isinstance(result.get('date'), datetime.date) else "N/A"
                alert_message = f"""
🚨Examination Result!🚨

Title: {result['title']}

Date: {date_str}

Link: {result['link']}
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

    def process_new_results(self, bot, existing_titles=None, existing_urls=None):
        import gc
        try:
            logger.info("Checking for new results")
            new_results = self.scrape_results(existing_titles, existing_urls)
            logger.info(f"Found {len(new_results)} new results")

            for result in new_results:
                try:
                    logger.info(f"Processing result: {result['title']}")

                    try:
                        extraction = self.summarizer.categorize_text(result['title'])
                    except SummarizationError:
                        logger.exception("Text categorization failed for result")
                        logger.warning(f"Strict requirement not met: Skipping result '{result['title']}' due to categorization failure.")
                        continue

                    if not extraction:
                        logger.error("Strict requirement not met: Extraction yielded empty result.")
                        continue

                    # Sleep to respect the Gemini API free tier rate limit (15 requests per minute)
                    time.sleep(5)

                    result_data = {
                        'title': result['title'],
                        'link': result['link'],
                        'date': result['date'],
                        'summary': extraction.summary or '',
                        'target_bhavana': extraction.target_bhavana,
                        'target_department': extraction.target_department,
                        'is_general': extraction.is_general,
                        'status': 'New'
                    }

                    added_record = self.storage.add_notice(result_data)

                    if added_record:
                        logger.info("Finding matching subscribers...")
                        matching_users = self.storage.get_matching_subscribers(result_data)
                        logger.info(f"Sending alerts to {len(matching_users)} matched users")
                        
                        self.send_telegram_alerts(bot, result, result_data['summary'], matching_users)
                        # Update status to 'Sent'
                        self.storage.update_notice_status(added_record['id'], 'Sent')
                        logger.info("Result processed successfully")
                    else:
                        logger.warning(f"Result '{result['title']}' was not added to Supabase, skipping alerts.")

                except Exception:
                    logger.exception("Result processing error")
                finally:
                    # Clear memory for each result processed
                    if 'file_bytes' in locals():
                        del file_bytes
                    if 'extraction' in locals():
                        del extraction

        except Exception:
            logger.exception("Error in process_new_results")
        finally:
            # Force garbage collection
            gc.collect()
