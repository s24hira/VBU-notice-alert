import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

logger = logging.getLogger(__name__)
load_dotenv()

class SupabaseStorage:
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")

        if not all([self.supabase_url, self.supabase_key]):
            logger.error("SUPABASE_URL or SUPABASE_KEY not set in environment variables.")
            raise ValueError("Supabase credentials missing.")

        self.supabase: Client = create_client(self.supabase_url, self.supabase_key)

    def reconnect(self):
        """Recreate the Supabase client to flush accumulated httpx connection pool state.
        
        The supabase-py client uses httpx internally. Over many hours of operation,
        the httpx connection pool accumulates SSL contexts and connection metadata.
        Recreating the client releases all of that back to the garbage collector.
        """
        logger.info("Recreating Supabase client to flush connection pool state")
        self.supabase = create_client(self.supabase_url, self.supabase_key)

    # User management
    def add_user(self, chat_id, username=None):
        # We might not know their selections yet, but we can insert them with defaults or leave them null.
        # However, the categorisation logic requires them to complete the flow.
        # This add_user is kept for basic backwards compatibility with ping/status if needed,
        # but realistically they need to do /start fully.
        try:
            # Check if user exists (fetching only the ID instead of all columns)
            response = self.supabase.table('subscribers').select('telegram_chat_id').eq('telegram_chat_id', chat_id).execute()
            if not response.data:
                self.supabase.table('subscribers').insert({
                    'telegram_chat_id': chat_id
                }).execute()
                logger.info(f"User {chat_id} added to Supabase.")
                return True
            return False
        except Exception as e:
            logger.error(f"Error adding user {chat_id} to Supabase: {type(e).__name__}")
            return False

    def upsert_subscriber(self, chat_id, bhavana, department, name=None):
        try:
            payload = {
                'telegram_chat_id': chat_id,
                'bhavana': bhavana,
                'department': department
            }
            if name:
                payload['name'] = name
                
            self.supabase.table('subscribers').upsert(payload).execute()
            logger.info(f"Subscriber {chat_id} upserted with selections: {bhavana}, {department}, name: {name}.")
            return True
        except Exception as e:
            logger.error(f"Error upserting subscriber {chat_id}: {type(e).__name__}")
            return False

    def get_subscriber(self, chat_id):
        try:
            response = self.supabase.table('subscribers').select('*').eq('telegram_chat_id', chat_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error fetching subscriber {chat_id}: {type(e).__name__}")
            return None

    def delete_subscriber(self, chat_id):
        try:
            self.supabase.table('subscribers').delete().eq('telegram_chat_id', chat_id).execute()
            logger.info(f"Subscriber {chat_id} deleted from Supabase.")
            return True
        except Exception as e:
            logger.error(f"Error deleting subscriber {chat_id}: {type(e).__name__}")
            return False

    def get_all_users(self):
        # Returns all users (fallback)
        try:
            response = self.supabase.table('subscribers').select('telegram_chat_id').execute()
            return [user['telegram_chat_id'] for user in response.data]
        except Exception as e:
            logger.error(f"Error getting all users: {type(e).__name__}")
            return []

    def get_matching_subscribers(self, notice_data):
        # notice_data should contain target_bhavana, target_department, is_general
        try:
            if notice_data.get('is_general', False):
                return self.get_all_users()

            target_bhavana = notice_data.get('target_bhavana') or "Central Administration / Office"
            target_department = notice_data.get('target_department')

            query = self.supabase.table('subscribers').select('telegram_chat_id')

            query = query.eq('bhavana', target_bhavana)
            if target_department:
                query = query.in_('department', [target_department, 'All'])

            response = query.execute()
            return [user['telegram_chat_id'] for user in response.data]
        except Exception as e:
            logger.error(f"Error fetching matching subscribers: {type(e).__name__}")
            return self.get_all_users() # Fallback to all if filtering fails

    # Notice management
    def add_notice(self, notice_data):
        try:
            title = notice_data.get('title', '')
            link = notice_data.get('link', '')

            # Escape double quotes in title
            safe_title = title.replace('"', '\\"')
            
            # Check if notice already exists by title or link using a single OR query
            duplicate = self.supabase.table('notices').select('id').or_(f'title.eq."{safe_title}",link.eq."{link}"').execute()
            
            if duplicate.data:
                logger.info(f"Notice '{title}' already exists. Skipping.")
                return None

            date_val = notice_data.get('date')
            if isinstance(date_val, datetime):
                date_val = date_val.isoformat()

            new_notice = {
                'title': notice_data.get('title'),
                'link': notice_data.get('link'),
                'target_bhavana': notice_data.get('target_bhavana') or "Central Administration / Office",
                'target_department': notice_data.get('target_department'),
                'is_general': notice_data.get('is_general', False),
                'date': date_val,
                'summary': notice_data.get('summary', ''),
                'status': notice_data.get('status', 'New')
            }
            
            res = self.supabase.table('notices').insert(new_notice).execute()
            if res.data:
                logger.info(f"Notice '{notice_data.get('title')}' added to Supabase.")
                return res.data[0]
            return None
        except Exception as e:
            logger.error(f"Failed to add notice '{notice_data.get('title')}': {type(e).__name__}")
            return None

    def get_existing_notices(self, limit=100):
        """Fetches the most recent notice titles and links for quick deduplication.
        We only need recent ones because the scraper only checks the top 10 items on the website.
        Older duplicates are caught by the specific query in add_notice()."""
        titles = set()
        links = set()
        try:
            # Fetch only the latest records to save memory and API calls
            response = self.supabase.table('notices').select('title, link').order('id', desc=True).limit(limit).execute()
            data = response.data
            
            if data:
                for item in data:
                    if item.get('title'):
                        titles.add(item['title'].strip())
                    if item.get('link'):
                        links.add(item['link'].strip())
            
            return titles, links
        except Exception as e:
            logger.error(f"Error fetching existing notices: {type(e).__name__}")
            return set(), set()

    def update_notice_status(self, record_id, status):
        try:
            res = self.supabase.table('notices').update({'status': status}).eq('id', record_id).execute()
            if res.data:
                logger.info(f"Notice {record_id} status updated to {status}.")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to update notice {record_id} status: {type(e).__name__}")
            return False