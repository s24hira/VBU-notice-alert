import os
import logging
import requests
import datetime
from bs4 import BeautifulSoup
import time

from bot.utils.summarizer import GeminiPDFSummarizer, SummarizationError, NoticeExtraction
from bot.storage import SupabaseStorage

logger = logging.getLogger(__name__)

class ResultProcessor:
    def __init__(self, summarizer: GeminiPDFSummarizer, storage: SupabaseStorage, website_url: str):
        self.summarizer = summarizer
        self.storage = storage
        self.website_url = website_url
        
        # Suppress insecure request warnings if fetching samarth without verify
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def scrape_results(self, max_retries=3):
        for attempt in range(max_retries):
            try:
                with requests.get(self.website_url, timeout=30, verify=False) as response:
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
                existing_urls = self.storage.get_all_notice_urls()
                logger.info(f"Fetched {len(existing_urls)} existing URLs from storage.")

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

                    if pdf_link not in existing_urls:
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
                logger.error(f"Error scraping results (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(5)
                
        return []

    def download_pdf_bytes(self, pdf_url, max_retries=3):
        for attempt in range(max_retries):
            try:
                # Some samarth links might need verify=False
                with requests.get(pdf_url, timeout=30, verify=False) as response:
                    if not response.headers.get('content-type', '').startswith('application/pdf'):
                        # S3 might return binary/octet-stream sometimes, so we check content
                        if b'%PDF-' not in response.content[:50]:
                            logger.error("Downloaded file does not appear to be a PDF")
                            return None

                    content = response.content
                    if len(content) < 100:
                        logger.error("Downloaded PDF file is too small")
                        return None

                    return content

            except Exception as e:
                logger.error(f"PDF download error (attempt {attempt + 1}/{max_retries}): {e}")
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
                logger.error(f"Telegram message send error to user {user_id}: {e}")

    def process_new_results(self, bot):
        import gc
        try:
            logger.info("Checking for new results")
            new_results = self.scrape_results()
            logger.info(f"Found {len(new_results)} new results")

            for result in new_results:
                try:
                    logger.info(f"Processing result: {result['title']}")
                    pdf_bytes = self.download_pdf_bytes(result['link'])
                    if not pdf_bytes:
                        continue

                    logger.info("Generating summary using Gemini")
                    try:
                        extraction = self.summarizer.summarize_pdf(pdf_bytes)
                    except SummarizationError as e:
                        logger.error(f"Summarization failed: {e}")
                        logger.warning(f"Strict requirement not met: Skipping result '{result['title']}' due to summarization failure.")
                        continue
                    
                    if not extraction or not extraction.summary:
                        logger.error("Strict requirement not met: Extraction yielded empty summary.")
                        logger.warning(f"Skipping result '{result['title']}'.")
                        continue

                    # Sleep to respect the Gemini API free tier rate limit
                    time.sleep(5)

                    result_data = {
                        'title': result['title'],
                        'link': result['link'],
                        'date': result['date'],
                        'summary': extraction.summary,
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

                except Exception as e:
                    logger.error(f"Result processing error: {e}")
                finally:
                    # Clear memory for each result processed
                    if 'pdf_bytes' in locals():
                        del pdf_bytes
                    if 'extraction' in locals():
                        del extraction

        except Exception as e:
            logger.error(f"Error in process_new_results: {e}")
        finally:
            # Force garbage collection
            gc.collect()
