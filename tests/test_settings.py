import unittest
from unittest.mock import MagicMock, patch
from bot.handlers import BotHandlers, _escape_markdown
from bot.storage import SupabaseStorage
from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP


class TestStorageSettings(unittest.TestCase):
    @patch.dict('os.environ', {'SUPABASE_URL': 'https://example.supabase.co', 'SUPABASE_KEY': 'fake-key'})
    @patch('bot.storage.create_client')
    def setUp(self, mock_create_client):
        self.mock_client = MagicMock()
        mock_create_client.return_value = self.mock_client
        self.storage = SupabaseStorage()

    def test_upsert_subscriber_with_general_notices(self):
        table_mock = MagicMock()
        self.mock_client.table.return_value = table_mock
        upsert_mock = MagicMock()
        table_mock.upsert.return_value = upsert_mock

        result = self.storage.upsert_subscriber(
            chat_id=12345,
            bhavana="Siksha Bhavana",
            department="Computer & System Sciences",
            name="Test User",
            receive_general_notices=False
        )

        self.assertTrue(result)
        table_mock.upsert.assert_called_once_with({
            'telegram_chat_id': 12345,
            'bhavana': "Siksha Bhavana",
            'department': "Computer & System Sciences",
            'name': "Test User",
            'receive_general_notices': False
        })

    def test_update_subscriber_whitelist(self):
        table_mock = MagicMock()
        self.mock_client.table.return_value = table_mock
        update_mock = MagicMock()
        table_mock.update.return_value = update_mock
        eq_mock = MagicMock()
        update_mock.eq.return_value = eq_mock

        # Test valid fields updated, invalid field ignored
        result = self.storage.update_subscriber(12345, {
            'name': 'New Name',
            'receive_general_notices': True,
            'malicious_field': 'drop table subscribers;'
        })

        self.assertTrue(result)
        table_mock.update.assert_called_once_with({
            'name': 'New Name',
            'receive_general_notices': True
        })
        update_mock.eq.assert_called_once_with('telegram_chat_id', 12345)

    def test_get_subscriber_defaults_general_notices(self):
        table_mock = MagicMock()
        self.mock_client.table.return_value = table_mock
        select_mock = MagicMock()
        table_mock.select.return_value = select_mock
        eq_mock = MagicMock()
        select_mock.eq.return_value = eq_mock
        eq_mock.execute.return_value.data = [{
            'telegram_chat_id': 12345,
            'bhavana': 'Siksha Bhavana',
            'department': 'Chemistry',
            'name': 'Alice',
            'receive_general_notices': None  # simulates legacy row
        }]

        sub = self.storage.get_subscriber(12345)
        self.assertIsNotNone(sub)
        self.assertTrue(sub['receive_general_notices'])

    def test_get_matching_subscribers_general(self):
        table_mock = MagicMock()
        self.mock_client.table.return_value = table_mock
        select_mock = MagicMock()
        table_mock.select.return_value = select_mock
        neq_mock = MagicMock()
        select_mock.neq.return_value = neq_mock
        neq_mock.execute.return_value.data = [
            {'telegram_chat_id': 111},
            {'telegram_chat_id': 222}
        ]

        users = self.storage.get_matching_subscribers({'is_general': True})
        self.assertEqual(users, [111, 222])
        select_mock.neq.assert_called_once_with('receive_general_notices', False)

    def test_get_matching_subscribers_targeted(self):
        table_mock = MagicMock()
        self.mock_client.table.return_value = table_mock
        select_mock = MagicMock()
        table_mock.select.return_value = select_mock
        eq_mock = MagicMock()
        select_mock.eq.return_value = eq_mock
        in_mock = MagicMock()
        eq_mock.in_.return_value = in_mock
        in_mock.execute.return_value.data = [{'telegram_chat_id': 333}]

        users = self.storage.get_matching_subscribers({
            'is_general': False,
            'target_bhavana': 'Siksha Bhavana',
            'target_department': 'Physics'
        })
        self.assertEqual(users, [333])
        select_mock.eq.assert_called_once_with('bhavana', 'Siksha Bhavana')
        eq_mock.in_.assert_called_once_with('department', ['Physics', 'All'])


class TestHandlersSettings(unittest.TestCase):
    def setUp(self):
        self.mock_bot = MagicMock()
        self.mock_storage = MagicMock()
        self.handlers = BotHandlers(self.mock_bot, self.mock_storage)

    def test_build_settings_text(self):
        sub = {
            'name': 'Test User',
            'bhavana': 'Siksha Bhavana',
            'department': 'Mathematics',
            'receive_general_notices': True
        }
        text = self.handlers._build_settings_text(sub)
        self.assertIn('Test User', text)
        self.assertIn('Tap any preference button below to edit:', text)
        # Verify redundant bullets are no longer in the text body
        self.assertNotIn('• **Bhavana:**', text)
        self.assertNotIn('• **Department:**', text)

    def test_build_settings_keyboard(self):
        sub = {
            'name': 'Test User',
            'bhavana': 'Siksha Bhavana',
            'department': 'Mathematics',
            'receive_general_notices': True
        }
        keyboard = self.handlers._build_settings_keyboard(sub)
        buttons = [btn for row in keyboard.keyboard for btn in row]
        callbacks = [btn.callback_data for btn in buttons]
        texts = [btn.text for btn in buttons]

        self.assertIn('SETTING_NAME', callbacks)
        self.assertIn('SETTING_BHAVANA', callbacks)
        self.assertIn('SETTING_DEPT', callbacks)
        self.assertIn('SETTING_TOGGLE_GEN', callbacks)
        self.assertIn('SETTINGS_DELETE_CONFIRM', callbacks)
        self.assertNotIn('SETTINGS_RESET', callbacks)

        gen_btn_text = [t for t in texts if 'General Notices' in t][0]
        self.assertIn('✅ ON', gen_btn_text)

    def test_escape_markdown(self):
        self.assertEqual(_escape_markdown("John_Doe*"), "John\\_Doe\\*")


class TestValidators(unittest.TestCase):
    def test_validate_name(self):
        from bot.utils.validators import validate_name

        # Valid names
        valid, name, err = validate_name("Shuvo Hira")
        self.assertTrue(valid)
        self.assertEqual(name, "Shuvo Hira")

        # Strip markdown chars
        valid, name, err = validate_name("John*Doe_")
        self.assertTrue(valid)
        self.assertEqual(name, "JohnDoe")

        # Empty name
        valid, name, err = validate_name("   ")
        self.assertFalse(valid)

        # Command as name
        valid, name, err = validate_name("/settings")
        self.assertFalse(valid)

        # Overly long name
        valid, name, err = validate_name("A" * 105)
        self.assertFalse(valid)

    def test_validate_bhavana_and_department(self):
        from bot.utils.validators import validate_bhavana, validate_department

        self.assertTrue(validate_bhavana("Siksha Bhavana"))
        self.assertFalse(validate_bhavana("Fake Bhavana"))

        self.assertTrue(validate_department("Siksha Bhavana", "Computer & System Sciences"))
        self.assertTrue(validate_department("Siksha Bhavana", "All"))
        self.assertFalse(validate_department("Siksha Bhavana", "NonExistentDept"))
        self.assertFalse(validate_department("Siksha Bhavana", "Agronomy"))  # Agronomy belongs to Palli Siksha Bhavana


if __name__ == '__main__':
    unittest.main()
