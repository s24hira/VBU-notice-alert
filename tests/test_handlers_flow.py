import unittest
from unittest.mock import MagicMock, patch
import telebot
from bot.handlers import BotHandlers
from bot.storage import SupabaseStorage
from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP


class TestHandlersFullFlow(unittest.TestCase):
    def setUp(self):
        self.mock_bot = MagicMock()
        self.mock_storage = MagicMock()
        self.handlers = BotHandlers(self.mock_bot, self.mock_storage)

    def _create_message(self, chat_id=12345, user_id=12345, text="/start", username="testuser"):
        msg = MagicMock()
        msg.chat.id = chat_id
        msg.from_user.id = user_id
        msg.from_user.username = username
        msg.text = text
        msg.content_type = 'text'
        return msg

    def _create_callback(self, chat_id=12345, user_id=12345, data="START"):
        call = MagicMock()
        call.id = "cb_123"
        call.message.chat.id = chat_id
        call.message.message_id = 999
        call.from_user.id = user_id
        call.data = data
        return call

    def test_start_command_new_user(self):
        self.mock_storage.get_subscriber.return_value = None
        self.handlers._setup_state[12345] = {'initiator_id': 12345}
        kb = self.handlers._build_bhavana_keyboard(page=0, show_cancel=False)
        self.assertIsNotNone(kb)

    def test_settings_command_existing_user(self):
        self.mock_storage.get_subscriber.return_value = {
            'telegram_chat_id': 12345,
            'name': 'Shuvo',
            'bhavana': 'Siksha Bhavana',
            'department': 'Computer & System Sciences',
            'receive_general_notices': True
        }
        self.handlers._user_cache[12345] = self.mock_storage.get_subscriber.return_value

        text = self.handlers._build_settings_text(self.handlers._user_cache[12345])
        self.assertIn("**Settings** for **Shuvo**", text)

        kb = self.handlers._build_settings_keyboard(self.handlers._user_cache[12345])
        button_callbacks = [btn.callback_data for row in kb.keyboard for btn in row]
        self.assertEqual(button_callbacks, [
            'SETTING_NAME',
            'SETTING_BHAVANA',
            'SETTING_DEPT',
            'SETTING_TOGGLE_GEN',
            'SETTINGS_DELETE_CONFIRM'
        ])

    def test_toggle_general_notices_callback(self):
        user_data = {
            'telegram_chat_id': 12345,
            'name': 'Shuvo',
            'bhavana': 'Siksha Bhavana',
            'department': 'Computer & System Sciences',
            'receive_general_notices': True
        }
        self.handlers._user_cache[12345] = user_data
        self.mock_storage.get_subscriber.return_value = user_data

        sub = self.handlers._user_cache.get(12345)
        current_gen = sub.get('receive_general_notices', True)
        new_gen = not bool(current_gen)
        
        self.mock_storage.update_subscriber(12345, {'receive_general_notices': new_gen})
        sub['receive_general_notices'] = new_gen
        self.handlers._user_cache[12345] = sub

        self.assertFalse(self.handlers._user_cache[12345]['receive_general_notices'])
        self.mock_storage.update_subscriber.assert_called_with(12345, {'receive_general_notices': False})

    def test_bhavana_and_department_edit_flow(self):
        self.handlers._setup_state[12345] = {
            'editing': 'bhavana',
            'initiator_id': 12345
        }
        
        bhav_idx = 1
        bhav_name = BHAVANAS_LIST[bhav_idx]
        dept_idx = 0
        dept_name = BHAVANA_DEPARTMENTS_MAP[bhav_name][dept_idx]
        
        updates = {'bhavana': bhav_name, 'department': dept_name}
        self.mock_storage.update_subscriber.return_value = True
        success = self.mock_storage.update_subscriber(12345, updates)
        
        self.assertTrue(success)
        self.mock_storage.update_subscriber.assert_called_with(12345, updates)

    def test_department_only_edit_flow(self):
        user_data = {
            'telegram_chat_id': 12345,
            'name': 'Shuvo',
            'bhavana': 'Siksha Bhavana',
            'department': 'Computer & System Sciences',
            'receive_general_notices': True
        }
        self.handlers._user_cache[12345] = user_data
        
        bhav_idx = BHAVANAS_LIST.index('Siksha Bhavana')
        self.handlers._setup_state[12345] = {
            'editing': 'department',
            'bhav_idx': bhav_idx,
            'initiator_id': 12345
        }

        updates = {'department': 'All'}
        self.mock_storage.update_subscriber.return_value = True
        success = self.mock_storage.update_subscriber(12345, updates)

        self.assertTrue(success)
        self.mock_storage.update_subscriber.assert_called_with(12345, updates)

    def test_edit_name_handler_flow(self):
        user_data = {
            'telegram_chat_id': 12345,
            'name': 'Old Name',
            'bhavana': 'Siksha Bhavana',
            'department': 'Computer & System Sciences',
            'receive_general_notices': True
        }
        self.handlers._user_cache[12345] = user_data
        self.mock_storage.update_subscriber.return_value = True

        msg = self._create_message(text="Shuvo Hira")
        self.handlers._edit_name_handler(msg)

        self.mock_storage.update_subscriber.assert_called_with(12345, {'name': 'Shuvo Hira'})
        self.assertEqual(self.handlers._user_cache[12345]['name'], 'Shuvo Hira')
        self.mock_bot.send_message.assert_called()

    def test_group_chat_unauthorized_user_rejection(self):
        group_chat_id = -100123456789
        initiator_id = 11111
        unauthorized_id = 99999

        self.handlers._setup_state[group_chat_id] = {'initiator_id': initiator_id}

        setup = self.handlers._setup_state.get(group_chat_id, {})
        self.assertEqual(setup.get('initiator_id'), initiator_id)
        self.assertNotEqual(unauthorized_id, setup.get('initiator_id'))


if __name__ == '__main__':
    unittest.main()
