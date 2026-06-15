import os
import logging
import requests
import uuid
from dotenv import load_dotenv
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)
load_dotenv()

class JsonbinStorage:
    def __init__(self):
        self.api_key = os.getenv('JSONBIN_API_KEY')
        self.bin_id = os.getenv('JSONBIN_BIN_ID')
        self.base_url = "https://api.jsonbin.io/v3/b"

        if not all([self.api_key, self.bin_id]):
            logger.error("JSONBIN_API_KEY or JSONBIN_BIN_ID not set in environment variables.")
            raise ValueError("JSONBin credentials missing.")

        self.headers = {
            'X-Master-Key': self.api_key,
            'Content-Type': 'application/json'
        }

        self.session = requests.Session()
        self.session.headers.update(self.headers)

        # Initialize the cache
        self.cache = None
        self.cache_time = None
        self.cache_ttl = timedelta(minutes=5)
        
        # Initial fetch to verify connection
        self._fetch_data()

    def _fetch_data(self):
        if self.cache is not None and (datetime.now() - self.cache_time < self.cache_ttl):
            return self.cache

        try:
            url = f"{self.base_url}/{self.bin_id}/latest"
            response = self.session.get(url)
            if response.status_code == 200:
                data = response.json().get('record', {})
                # Ensure structure exists
                if 'users' not in data:
                    data['users'] = []
                if 'notices' not in data:
                    data['notices'] = []
                
                self.cache = data
                self.cache_time = datetime.now()
                return self.cache
            else:
                logger.error(f"Error fetching data from JSONBin: {response.status_code} - {response.text}")
                return {'users': [], 'notices': []}
        except Exception as e:
            logger.error(f"Exception fetching data from JSONBin: {e}")
            return {'users': [], 'notices': []}

    def _save_data(self, data):
        try:
            url = f"{self.base_url}/{self.bin_id}"
            response = self.session.put(url, json=data)
            if response.status_code == 200:
                self.cache = data
                self.cache_time = datetime.now()
                return True
            else:
                logger.error(f"Error saving data to JSONBin: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Exception saving data to JSONBin: {e}")
            return False

    # User management
    def add_user(self, chat_id, username=None):
        if not self.user_exists(chat_id):
            data = self._fetch_data()
            joined_date = datetime.now(timezone.utc).isoformat()
            user_data = {
                'chat_id': chat_id,
                'joined_date': joined_date
            }
            if username:
                user_data['username'] = username
            
            data['users'].append(user_data)
            if self._save_data(data):
                logger.info(f"User {chat_id} added to JSONBin.")
                return True
            else:
                logger.error(f"Failed to add user {chat_id} to JSONBin.")
                return False
        logger.info(f"User {chat_id} already exists. Not adding.")
        return False

    def user_exists(self, chat_id):
        data = self._fetch_data()
        for user in data.get('users', []):
            if user.get('chat_id') == chat_id:
                return True
        return False

    def get_all_users(self):
        data = self._fetch_data()
        return [user.get('chat_id') for user in data.get('users', []) if 'chat_id' in user]

    # Notice management
    def add_notice(self, notice_data):
        data = self._fetch_data()
        
        # Check if notice already exists by title or link
        for notice in data.get('notices', []):
            if notice.get('title') == notice_data.get('title') or notice.get('link') == notice_data.get('link'):
                logger.info(f"Notice '{notice_data.get('title')}' already exists in JSONBin. Skipping.")
                return None

        # Ensure date is in ISO format if present
        if 'date' in notice_data and isinstance(notice_data['date'], datetime):
            notice_data['date'] = notice_data['date'].isoformat()
        elif 'date' in notice_data and not isinstance(notice_data['date'], str):
            notice_data['date'] = None

        new_notice = {
            'id': str(uuid.uuid4()),
            'title': notice_data.get('title'),
            'link': notice_data.get('link'),
            'date': notice_data.get('date'),
            'summary': notice_data.get('summary', ''),
            'status': notice_data.get('status', 'New')
        }
        
        data['notices'].append(new_notice)
        if self._save_data(data):
            logger.info(f"Notice '{notice_data.get('title')}' added to JSONBin.")
            return new_notice
        else:
            logger.error(f"Failed to add notice '{notice_data.get('title')}' to JSONBin.")
            return None

    def notice_exists(self, title, link):
        data = self._fetch_data()
        for notice in data.get('notices', []):
            if notice.get('status') == 'Sent' and (notice.get('title') == title or notice.get('link') == link):
                return True
        return False

    def get_all_notice_urls(self):
        data = self._fetch_data()
        return {notice.get('link') for notice in data.get('notices', []) if 'link' in notice}

    def update_notice_status(self, record_id, status):
        data = self._fetch_data()
        updated = False
        for notice in data.get('notices', []):
            if notice.get('id') == record_id:
                notice['status'] = status
                updated = True
                break
        
        if updated:
            if self._save_data(data):
                logger.info(f"Notice {record_id} status updated to {status}.")
                return True
            else:
                logger.error(f"Failed to update notice {record_id} status in JSONBin.")
                return False
        else:
            logger.error(f"Notice {record_id} not found in JSONBin for status update.")
            return False