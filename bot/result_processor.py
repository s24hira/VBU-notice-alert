import os
import logging
import requests
import datetime
from bs4 import BeautifulSoup
import time
from urllib.parse import urlparse
import certifi

from bot.utils.summarizer import GeminiPDFSummarizer, SummarizationError, NoticeExtraction
from bot.storage import SupabaseStorage

logger = logging.getLogger(__name__)

class ResultProcessor:
    def __init__(self, summarizer: GeminiPDFSummarizer, storage: SupabaseStorage, website_url: str):
        self.summarizer = summarizer
        self.storage = storage
        self.website_url = website_url
        self.MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB
        self.ALLOWED_DOMAINS = [
            'visvabharati.ac.in',
            'visvabharati.samarth.edu.in',
            'amazonaws.com',
        ]

    def _is_safe_url(self, url):
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return False
            return any(parsed.hostname and parsed.hostname.endswith(domain) for domain in self.ALLOWED_DOMAINS)
        except Exception:
            return False
        
        import certifi

    def scrape_results(self, max_retries=3):
        for attempt in range(max_retries):
            try:
                with requests.get(self.website_url, timeout=30, verify=certifi.where()) as response:
                    soup = BeautifulSoup(response.content, 'html.parser')

                tables = soup.find_all('table')
                if not tables:
                    logger.error("Could not find any tables on Samarth result page.")
                    continue
                
                tbody = tables[0].find('tbody')
                if not tbody:
                    logger.error("Could not find tbody in Samarth table.")
                    continue

                rows = tbody.find_all('tr', attrs={'data-key': True})
                if not rows:
                    logger.error("Could not find any data rows in Samarth table.")
                    continue

                new_results = []
                existing_titles, existing_urls = self.storage.get_existing_notices()
                logger.info(f"Fetched {len(existing_urls)} existing records from storage.")

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
                            logger.error(f"Could not parse date: {date_string}")

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

            except Exception as e:
                logger.error(f"Error scraping results (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
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
                        # S3 might return binary/octet-stream sometimes, so we check content
                        # Since it's streaming, we need to read the first chunk to check
                        pass # Moved check below after reading first chunk

                    chunks = []
                    downloaded = 0
                    first_chunk = True
                    for chunk in response.iter_content(chunk_size=8192):
                        if first_chunk:
                            if not response.headers.get('content-type', '').startswith('application/pdf') and b'%PDF-' not in chunk[:50]:
                                logger.error("Downloaded file does not appear to be a PDF")
                                return None
                            first_chunk = False

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

            except Exception as e:
                logger.error(f"PDF download error (attempt {attempt + 1}/{max_retries}): {type(e).__name__}")
                if attempt < max_retries - 1:
                    time.sleep(5)

        return None

    def send_telegram_alerts(self, bot, result, summary_text, user_ids):
        for user_id in user_ids:
            try:
                date_str = result['date'].strftime('%b %d, %Y') if isinstance(result.get('date'), datetime.date) else "N/A"
                alert_message = f"""
🚨Examination Result!🚨

Title: {result['title']}

Date: {date_str}

PDF Link: {result['link']}
                """
                bot.send_message(user_id, alert_message)

                if summary_text:
                    summary_message = f"""
📋 AI Summary:

{summary_text}
                    """
                    bot.send_message(user_id, summary_message)

            except Exception as e:
                logger.error(f"Telegram message send error to user {user_id}: {type(e).__name__}")

    def process_new_results(self, bot):
        import gc
        try:
            logger.info("Checking for new results")
            new_results = self.scrape_results()
            logger.info(f"Found {len(new_results)} new results")

            for result in new_results:
                try:
                    logger.info(f"Processing result: {result['title']}")

                    result_data = {
                        'title': result['title'],
                        'link': result['link'],
                        'date': result['date'],
                        'summary': '',
                        'target_bhavana': None,
                        'target_department': None,
                        'is_general': True,
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

                except Exception as e:
                    logger.error(f"Result processing error: {type(e).__name__}")

        except Exception as e:
            logger.error(f"Error in process_new_results: {type(e).__name__}")
        finally:
            # Force garbage collection
            gc.collect()
