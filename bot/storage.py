import os
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

from bot.utils.validators import (
    validate_chat_id,
    validate_name,
    validate_bhavana,
    validate_department,
    validate_general_notices
)

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
        try:
            if not validate_chat_id(chat_id):
                logger.warning(f"Invalid chat_id {chat_id} rejected in add_user.")
                return False

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

    ALLOWED_SUBSCRIBER_FIELDS = {'name', 'bhavana', 'department', 'receive_general_notices'}

    def upsert_subscriber(self, chat_id, bhavana, department, name=None, receive_general_notices=True):
        try:
            if not validate_chat_id(chat_id):
                logger.warning(f"Invalid chat_id {chat_id} in upsert_subscriber.")
                return False
            if not validate_bhavana(bhavana):
                logger.warning(f"Invalid bhavana '{bhavana}' in upsert_subscriber.")
                return False
            if not validate_department(bhavana, department):
                logger.warning(f"Invalid department '{department}' for bhavana '{bhavana}' in upsert_subscriber.")
                return False
            if not validate_general_notices(receive_general_notices):
                logger.warning(f"Invalid receive_general_notices value in upsert_subscriber.")
                return False

            payload = {
                'telegram_chat_id': chat_id,
                'bhavana': bhavana,
                'department': department,
                'receive_general_notices': receive_general_notices
            }
            if name:
                is_valid, sanitized_name, err = validate_name(name)
                if not is_valid:
                    logger.warning(f"Invalid name '{name}' in upsert_subscriber: {err}")
                    return False
                payload['name'] = sanitized_name
                
            self.supabase.table('subscribers').upsert(payload).execute()
            logger.info(f"Subscriber {chat_id} upserted with selections: {bhavana}, {department}, name: {name}, receive_general_notices: {receive_general_notices}.")
            return True
        except Exception as e:
            logger.error(f"Error upserting subscriber {chat_id}: {type(e).__name__}")
            return False

    def update_subscriber(self, chat_id, updates: dict):
        """Update specific fields for an existing subscriber with strict validation and field whitelisting."""
        try:
            if not validate_chat_id(chat_id):
                logger.warning(f"Invalid chat_id {chat_id} in update_subscriber.")
                return False

            sanitized_payload = {}
            for k, v in updates.items():
                if k not in self.ALLOWED_SUBSCRIBER_FIELDS:
                    continue

                if k == 'name':
                    is_valid, sanitized_name, err = validate_name(v)
                    if not is_valid:
                        logger.warning(f"Invalid name in update_subscriber for {chat_id}: {err}")
                        return False
                    sanitized_payload['name'] = sanitized_name
                elif k == 'bhavana':
                    if not validate_bhavana(v):
                        logger.warning(f"Invalid bhavana '{v}' in update_subscriber for {chat_id}.")
                        return False
                    sanitized_payload['bhavana'] = v
                elif k == 'department':
                    # If bhavana is also being updated, validate against it
                    target_bhavana = updates.get('bhavana')
                    if not target_bhavana:
                        sub = self.get_subscriber(chat_id)
                        target_bhavana = sub.get('bhavana') if sub else None
                    if target_bhavana and not validate_department(target_bhavana, v):
                        logger.warning(f"Invalid department '{v}' for bhavana '{target_bhavana}' in update_subscriber for {chat_id}.")
                        return False
                    sanitized_payload['department'] = v
                elif k == 'receive_general_notices':
                    if not validate_general_notices(v):
                        logger.warning(f"Invalid receive_general_notices value in update_subscriber for {chat_id}.")
                        return False
                    sanitized_payload['receive_general_notices'] = v

            if not sanitized_payload:
                logger.warning(f"No valid fields to update for subscriber {chat_id}.")
                return False

            self.supabase.table('subscribers').update(sanitized_payload).eq('telegram_chat_id', chat_id).execute()
            logger.info(f"Subscriber {chat_id} updated with fields: {list(sanitized_payload.keys())}.")
            return True
        except Exception as e:
            logger.error(f"Error updating subscriber {chat_id}: {type(e).__name__}")
            return False

    def get_subscriber(self, chat_id):
        try:
            response = self.supabase.table('subscribers').select('*').eq('telegram_chat_id', chat_id).execute()
            if response.data:
                subscriber = response.data[0]
                # Default receive_general_notices to True if missing/None for backward compatibility
                if subscriber.get('receive_general_notices') is None:
                    subscriber['receive_general_notices'] = True
                return subscriber
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
                # Only return users who want general notices (receive_general_notices is not False)
                response = self.supabase.table('subscribers').select('telegram_chat_id').neq('receive_general_notices', False).execute()
                return [user['telegram_chat_id'] for user in response.data]

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

    def is_notice_exists(self, title: str, link: str) -> bool:
        """Check if a notice or result already exists in the database by title or link."""
        try:
            if not title and not link:
                return False
            safe_title = (title or '').replace('"', '\\"')
            safe_link = (link or '').replace('"', '\\"')
            
            duplicate = self.supabase.table('notices').select('id').or_(f'title.eq."{safe_title}",link.eq."{safe_link}"').execute()
            return bool(duplicate.data)
        except Exception as e:
            logger.error(f"Error checking if notice exists: {type(e).__name__}")
            return False

    # Notice management
    def add_notice(self, notice_data):
        try:
            title = notice_data.get('title', '')
            link = notice_data.get('link', '')

            if self.is_notice_exists(title, link):
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