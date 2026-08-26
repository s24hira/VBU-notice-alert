import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import logging
import time
from functools import wraps

import re
from collections import defaultdict
from cachetools import TTLCache
from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP
from bot.utils.validators import validate_name

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 6
# Maximum pages to allow for bhavana / department pagination
_MAX_BHAVANA_PAGE = (len(BHAVANAS_LIST) - 1) // ITEMS_PER_PAGE


def _escape_markdown(text: str) -> str:
    """Escape special characters for Telegram Markdown (v1) to prevent injection."""
    # Characters that have special meaning in Telegram's Markdown v1
    return re.sub(r'([*_`\[\]])', r'\\\1', text)

class BotHandlers:
    def __init__(self, bot, storage):
        self.bot = bot
        self.storage = storage
        self._rate_limit = TTLCache(maxsize=200, ttl=60)
        self._known_users = set()
        self._user_is_existing = TTLCache(maxsize=100, ttl=1800)
        self._setup_state = TTLCache(maxsize=50, ttl=300)
        # Bounded TTL caches — prevent unbounded memory growth over time.
        # Entries expire after 30 mins; at most 100 concurrent users cached to save idle RAM.
        self._user_cache = TTLCache(maxsize=100, ttl=1800)
        self.setup_commands()

    def _is_rate_limited(self, chat_id, max_calls=5, window_seconds=60):
        now = time.time()
        if chat_id in self._rate_limit:
            count, window_start = self._rate_limit[chat_id]
            if now - window_start < window_seconds:
                if count >= max_calls:
                    return True
                self._rate_limit[chat_id] = (count + 1, window_start)
                return False
                
        self._rate_limit[chat_id] = (1, now)
        return False

    def ensure_user(self, func):
        @wraps(func)
        def wrapper(message):
            import threading
            user_id = message.chat.id
            if self._is_rate_limited(user_id):
                logger.warning(f"User {user_id} rate limited.")
                return
            if user_id not in self._known_users:
                username = message.from_user.username
                
                # Fire and forget: add user to DB in the background so it doesn't block the UI
                def _bg_add():
                    if self.storage.add_user(user_id, username):
                        logger.info(f"New user {user_id} added from {func.__name__}.")
                
                threading.Thread(target=_bg_add, daemon=True).start()
                self._known_users.add(user_id)
            return func(message)
        return wrapper

    def _build_settings_text(self, sub=None):
        sub = sub or {}
        name = sub.get('name')
        name_str = f" for **{_escape_markdown(name)}**" if name else ""
        return (
            f"🔧 **Settings**{name_str}\n\n"
            f"Tap any preference button below to edit:"
        )

    def _build_settings_keyboard(self, sub=None):
        markup = InlineKeyboardMarkup(row_width=1)
        sub = sub or {}
        name = sub.get('name', 'Edit')
        bhavana = sub.get('bhavana', 'Edit')
        department = sub.get('department', 'Edit')
        gen_enabled = sub.get('receive_general_notices', True)
        gen_status = "✅ ON" if gen_enabled else "❌ OFF"

        def _trunc(text, max_len=28):
            return text if len(text) <= max_len else text[:max_len - 3] + "..."

        markup.add(InlineKeyboardButton(f"👤 Name: {_trunc(name)}", callback_data="SETTING_NAME"))
        markup.add(InlineKeyboardButton(f"🏛️ Bhavana: {_trunc(bhavana)}", callback_data="SETTING_BHAVANA"))
        markup.add(InlineKeyboardButton(f"📚 Department: {_trunc(department)}", callback_data="SETTING_DEPT"))
        markup.add(InlineKeyboardButton(f"📢 General Notices: {gen_status}", callback_data="SETTING_TOGGLE_GEN"))
        markup.add(InlineKeyboardButton("🗑️ Delete Account", callback_data="SETTINGS_DELETE_CONFIRM"))
        return markup

    def _build_bhavana_keyboard(self, page=0, show_cancel=False, cancel_callback="CANCEL"):
        markup = InlineKeyboardMarkup(row_width=1)
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        
        for idx in range(start_idx, min(end_idx, len(BHAVANAS_LIST))):
            bhavana = BHAVANAS_LIST[idx]
            # Payload: B:{bhav_idx}
            markup.add(InlineKeyboardButton(bhavana, callback_data=f"B:{idx}"))
            
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◄ Back", callback_data=f"PB:{page-1}"))
        if end_idx < len(BHAVANAS_LIST):
            nav_buttons.append(InlineKeyboardButton("Next ►", callback_data=f"PB:{page+1}"))
            
        if nav_buttons:
            markup.add(*nav_buttons)
            
        if show_cancel:
            btn_text = "🔙 Back to Settings" if cancel_callback == "SETTINGS_BACK" else "❌ Cancel"
            markup.add(InlineKeyboardButton(btn_text, callback_data=cancel_callback))
        return markup

    def _build_dept_keyboard(self, bhav_idx, page=0, show_cancel=False, cancel_callback="CANCEL", back_callback="START"):
        markup = InlineKeyboardMarkup(row_width=1)
        bhavana_name = BHAVANAS_LIST[bhav_idx]
        depts = BHAVANA_DEPARTMENTS_MAP[bhavana_name]
        
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        
        if page == 0:
            markup.add(InlineKeyboardButton("🌟 All (Entire Bhavana)", callback_data=f"D:{bhav_idx}:-1"))
        
        for idx in range(start_idx, min(end_idx, len(depts))):
            dept = depts[idx]
            # Payload: D:{bhav_idx}:{dept_idx}
            markup.add(InlineKeyboardButton(dept, callback_data=f"D:{bhav_idx}:{idx}"))
            
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◄ Back", callback_data=f"PD:{bhav_idx}:{page-1}"))
        if end_idx < len(depts):
            nav_buttons.append(InlineKeyboardButton("Next ►", callback_data=f"PD:{bhav_idx}:{page+1}"))
            
        if nav_buttons:
            markup.add(*nav_buttons)
            
        # Add back button
        if back_callback:
            back_text = "🔙 Back to Bhavanas" if back_callback == "START" else "🔙 Back to Bhavana"
            markup.add(InlineKeyboardButton(back_text, callback_data=back_callback))
        if show_cancel:
            btn_text = "🔙 Back to Settings" if cancel_callback == "SETTINGS_BACK" else "❌ Cancel"
            markup.add(InlineKeyboardButton(btn_text, callback_data=cancel_callback))
        return markup

    def _save_name_final_handler(self, message):
        chat_id = message.chat.id
        
        try:
            if message.content_type != 'text':
                msg = self.bot.send_message(chat_id, "❌ Please send your name as a text message:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                return

            raw_name = message.text if message.text else ""
            if raw_name.strip().startswith('/'):
                self._setup_state.pop(chat_id, None)
                self.bot.send_message(chat_id, "❌ Setup cancelled. You can type /start to try again.")
                return

            is_valid, sanitized_name, error_msg = validate_name(raw_name)
            if not is_valid:
                msg = self.bot.send_message(chat_id, f"❌ {error_msg}\n\nPlease try again:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                return

            # Save name in the database
            setup_data = self._setup_state.get(chat_id)
            if not setup_data or not setup_data.get('bhavana') or not setup_data.get('department'):
                self.bot.send_message(
                    chat_id, 
                    "❌ Oops! We lost track of your Bhavana and Department selection. Please type /start to try again. 🙏"
                )
                return

            bhavana = setup_data.get('bhavana')
            department = setup_data.get('department')
            
            success = self.storage.upsert_subscriber(chat_id, bhavana, department, sanitized_name, receive_general_notices=True)
            
            if success:
                self._setup_state.pop(chat_id, None)
                self._user_is_existing[chat_id] = True
                self._user_cache[chat_id] = {
                    'telegram_chat_id': chat_id,
                    'bhavana': bhavana,
                    'department': department,
                    'name': sanitized_name,
                    'receive_general_notices': True
                }
                safe_name = _escape_markdown(sanitized_name)
                msg_text = (
                    f"✅ **Subscription Confirmed!**\n\n"
                    f"Welcome, **{safe_name}**!\n"
                    f"You will now receive targeted notices for:\n"
                    f"🏛️ Bhavana: {bhavana}\n"
                    f"📚 Department: {department}\n"
                    f"📢 General Notices: ✅ Enabled\n\n"
                    f"_(Use /settings to change preferences at any time)_"
                )
            else:
                msg_text = "❌ Oops! We had a small hiccup while saving your subscription. Please type /start to try again. 🙏"
                
            self.bot.send_message(
                chat_id, 
                msg_text, 
                parse_mode="Markdown"
            )
        except Exception:
            logger.exception(f"Error in _save_name_final_handler for user {chat_id}")
            self.bot.send_message(
                chat_id, 
                "❌ An unexpected error occurred. Please try again later."
            )

    def _edit_name_handler(self, message):
        chat_id = message.chat.id
        try:
            if message.content_type != 'text':
                msg = self.bot.send_message(chat_id, "❌ Please send your name as a text message:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._edit_name_handler)
                return

            raw_name = message.text if message.text else ""
            if raw_name.strip().startswith('/'):
                self._setup_state.pop(chat_id, None)
                self.bot.send_message(chat_id, "❌ Name change cancelled.")
                return

            is_valid, sanitized_name, error_msg = validate_name(raw_name)
            if not is_valid:
                msg = self.bot.send_message(chat_id, f"❌ {error_msg}\n\nPlease try again:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._edit_name_handler)
                return

            success = self.storage.update_subscriber(chat_id, {'name': sanitized_name})
            self._setup_state.pop(chat_id, None)

            if success:
                sub = self._user_cache.get(chat_id) or self.storage.get_subscriber(chat_id) or {}
                sub['name'] = sanitized_name
                self._user_cache[chat_id] = sub
                safe_name = _escape_markdown(sanitized_name)
                msg_text = (
                    f"✅ **Name Updated!**\n\n"
                    f"Your name has been updated to **{safe_name}**.\n\n"
                    + self._build_settings_text(sub)
                )
                self.bot.send_message(
                    chat_id,
                    msg_text,
                    reply_markup=self._build_settings_keyboard(sub),
                    parse_mode="Markdown"
                )
            else:
                self.bot.send_message(
                    chat_id,
                    "❌ Oops! Could not update your name. Please try again via /settings."
                )
        except Exception:
            logger.exception(f"Error in _edit_name_handler for user {chat_id}")
            self.bot.send_message(chat_id, "❌ An unexpected error occurred. Please try again later.")

    def setup_commands(self):
        @self.bot.message_handler(commands=['start'])
        @self.ensure_user
        def start_command(message):
            chat_id = message.chat.id
            if chat_id in self._user_cache:
                sub = self._user_cache[chat_id]
            else:
                sub = self.storage.get_subscriber(chat_id)
                if sub:
                    self._user_cache[chat_id] = sub
                    
            is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
            self._user_is_existing[chat_id] = is_existing
            
            if is_existing:
                safe_name = _escape_markdown(sub.get('name', 'User'))
                gen_status = "✅ Enabled" if sub.get('receive_general_notices', True) else "❌ Disabled"
                msg_text = (
                    f"👋 You are already subscribed to VBU notice alerts!\n\n"
                    f"**Current Configuration:**\n"
                    f"👤 **Name:** {safe_name}\n"
                    f"🏛️ **Bhavana:** {sub['bhavana']}\n"
                    f"📚 **Department:** {sub['department']}\n"
                    f"📢 **General Notices:** {gen_status}\n\n"
                    f"If you wish to change your configuration, please use /settings"
                )
                self.bot.send_message(chat_id, msg_text, parse_mode="Markdown")
                return
            
            # Start the setup flow directly if they are new or incomplete
            self._setup_state[chat_id] = {'initiator_id': message.from_user.id}
            msg_text = "👋 Welcome to the Visva-Bharati Notice Bot!\n\nPlease select your **Bhavana** to begin:"
            self.bot.send_message(
                chat_id, 
                msg_text, 
                reply_markup=self._build_bhavana_keyboard(page=0, show_cancel=False),
                parse_mode="Markdown"
            )
            self.bot.clear_step_handler_by_chat_id(chat_id)

        @self.bot.message_handler(commands=['settings'])
        @self.ensure_user
        def settings_command(message):
            chat_id = message.chat.id
            if chat_id in self._user_cache:
                sub = self._user_cache[chat_id]
            else:
                sub = self.storage.get_subscriber(chat_id)
                if sub:
                    self._user_cache[chat_id] = sub
                    
            is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
            self._user_is_existing[chat_id] = is_existing

            if not is_existing:
                self.bot.send_message(
                    chat_id,
                    "⚠️ You don't have an active subscription yet. Please use /start to set one up."
                )
                return

            # Track who opened the settings menu for group chat auth
            state = self._setup_state.get(chat_id, {})
            state['initiator_id'] = message.from_user.id
            self._setup_state[chat_id] = state

            msg_text = self._build_settings_text(sub)
            self.bot.send_message(
                chat_id,
                msg_text,
                reply_markup=self._build_settings_keyboard(sub),
                parse_mode="Markdown"
            )

        @self.bot.message_handler(commands=['status'])
        @self.ensure_user
        def status_command(message):
            status_msg = "Bot Status: ✅ Running\nUse /settings to configure your notification preferences."
            self.bot.reply_to(message, status_msg)

        @self.bot.message_handler(commands=['ping'])
        @self.ensure_user
        def ping_command(message):
            start_time = time.time()
            sent_message = self.bot.reply_to(message, "Pong!")
            latency = int((time.time() - start_time) * 1000)
            self.bot.edit_message_text(
                f"Pong! ({latency} ms)",
                chat_id=sent_message.chat.id,
                message_id=sent_message.message_id
            )

        # CALLBACK QUERY HANDLERS
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call):
            try:
                chat_id = call.message.chat.id
                user_id = call.from_user.id

                # Security for groups: only allow the user who initiated the setup 
                # to click the buttons. We track this via _setup_state.
                if chat_id < 0:  # Group chat
                    setup_state = self._setup_state.get(chat_id, {})
                    initiator_id = setup_state.get('initiator_id')
                    
                    if not initiator_id:
                        self.bot.answer_callback_query(call.id, "Session expired. Please type /start or /settings again.", show_alert=True)
                        return
                    if user_id != initiator_id:
                        self.bot.answer_callback_query(call.id, "Only the person who initiated this command can interact with it.", show_alert=True)
                        return
                else:
                    # In private chats, the button presser MUST be the chat owner
                    if user_id != chat_id:
                        self.bot.answer_callback_query(call.id, "Unauthorized action.")
                        return

                self.bot.answer_callback_query(call.id)
                data = call.data
                
                if data == "START":
                    chat_id = call.message.chat.id
                    is_existing = self._user_is_existing.get(chat_id, False)
                    msg_text = "Please select your **Bhavana**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(page=0, show_cancel=is_existing),
                        parse_mode="Markdown"
                    )
                    return

                if data == "CANCEL":
                    chat_id = call.message.chat.id
                    if chat_id in self._user_cache:
                        sub = self._user_cache[chat_id]
                    else:
                        sub = self.storage.get_subscriber(chat_id)
                        if sub:
                            self._user_cache[chat_id] = sub
                            
                    if sub and sub.get('bhavana') and sub.get('department') and sub.get('name'):
                        safe_name = _escape_markdown(sub.get('name', 'User'))
                        gen_status = "✅ Enabled" if sub.get('receive_general_notices', True) else "❌ Disabled"
                        msg_text = (
                            f"❌ **Settings change cancelled.**\n\n"
                            f"**Current Configuration:**\n"
                            f"👤 **Name:** {safe_name}\n"
                            f"🏛️ **Bhavana:** {sub['bhavana']}\n"
                            f"📚 **Department:** {sub['department']}\n"
                            f"📢 **General Notices:** {gen_status}"
                        )
                    else:
                        msg_text = "❌ **Setup cancelled.**\n\nYou can use /start to configure your subscription."
                    
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        parse_mode="Markdown"
                    )
                    return

                # ── Settings: Toggle General Notices ─────────────────────────
                if data == "SETTING_TOGGLE_GEN":
                    chat_id = call.message.chat.id
                    sub = self._user_cache.get(chat_id) or self.storage.get_subscriber(chat_id) or {}
                    current_gen = sub.get('receive_general_notices', True)
                    new_gen = not bool(current_gen)
                    
                    self.storage.update_subscriber(chat_id, {'receive_general_notices': new_gen})
                    sub['receive_general_notices'] = new_gen
                    self._user_cache[chat_id] = sub
                    
                    status_text = "enabled ✅" if new_gen else "disabled ❌"
                    self.bot.answer_callback_query(call.id, f"General notices {status_text}")
                    
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=self._build_settings_text(sub),
                        reply_markup=self._build_settings_keyboard(sub),
                        parse_mode="Markdown"
                    )
                    return

                # ── Settings: Edit Name ──────────────────────────────────────
                if data == "SETTING_NAME":
                    chat_id = call.message.chat.id
                    sub = self._user_cache.get(chat_id) or self.storage.get_subscriber(chat_id) or {}
                    safe_name = _escape_markdown(sub.get('name', 'User'))
                    
                    self._setup_state[chat_id] = {
                        'editing': 'name',
                        'initiator_id': call.from_user.id
                    }
                    
                    cancel_markup = InlineKeyboardMarkup(row_width=1)
                    cancel_markup.add(InlineKeyboardButton("🔙 Back to Settings", callback_data="SETTINGS_BACK"))
                    
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=f"✏️ **Edit Name**\n\nCurrent Name: **{safe_name}**\n\nPlease type your new name below:",
                        reply_markup=cancel_markup,
                        parse_mode="Markdown"
                    )
                    msg = self.bot.send_message(
                        chat_id,
                        "Type your new name:",
                        reply_markup=ForceReply(selective=True)
                    )
                    self.bot.register_next_step_handler(msg, self._edit_name_handler)
                    return

                # ── Settings: Edit Bhavana ───────────────────────────────────
                if data == "SETTING_BHAVANA":
                    chat_id = call.message.chat.id
                    self._setup_state[chat_id] = {
                        'editing': 'bhavana',
                        'initiator_id': call.from_user.id
                    }
                    msg_text = "🏛️ **Edit Bhavana**\n\nPlease select your new **Bhavana**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(page=0, show_cancel=True, cancel_callback="SETTINGS_BACK"),
                        parse_mode="Markdown"
                    )
                    return

                # ── Settings: Edit Department ─────────────────────────────────
                if data == "SETTING_DEPT":
                    chat_id = call.message.chat.id
                    sub = self._user_cache.get(chat_id) or self.storage.get_subscriber(chat_id) or {}
                    bhav_name = sub.get('bhavana')
                    
                    if bhav_name and bhav_name in BHAVANAS_LIST:
                        bhav_idx = BHAVANAS_LIST.index(bhav_name)
                        self._setup_state[chat_id] = {
                            'editing': 'department',
                            'bhav_idx': bhav_idx,
                            'initiator_id': call.from_user.id
                        }
                        msg_text = f"📚 **Edit Department**\n\nBhavana: **{bhav_name}**\n\nPlease select your **Department/Centre**:"
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text=msg_text,
                            reply_markup=self._build_dept_keyboard(
                                bhav_idx, 
                                page=0, 
                                show_cancel=True, 
                                cancel_callback="SETTINGS_BACK", 
                                back_callback=None
                            ),
                            parse_mode="Markdown"
                        )
                    else:
                        # Fallback to bhavana selection if current bhavana is invalid
                        self._setup_state[chat_id] = {
                            'editing': 'bhavana',
                            'initiator_id': call.from_user.id
                        }
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text="Please select your **Bhavana**:",
                            reply_markup=self._build_bhavana_keyboard(page=0, show_cancel=True, cancel_callback="SETTINGS_BACK"),
                            parse_mode="Markdown"
                        )
                    return

                # ── Settings: Delete Account (confirm prompt) ─────────────────
                if data == "SETTINGS_DELETE_CONFIRM":
                    chat_id = call.message.chat.id
                    confirm_markup = InlineKeyboardMarkup(row_width=1)
                    confirm_markup.add(
                        InlineKeyboardButton("⚠️ Yes, delete my account", callback_data="SETTINGS_DELETE_DO"),
                        InlineKeyboardButton("🔙 No, go back", callback_data="SETTINGS_BACK")
                    )
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=(
                            "🗑️ **Delete Account**\n\n"
                            "This will remove your subscription.\n"
                            "Are you sure you want to continue?"
                        ),
                        reply_markup=confirm_markup,
                        parse_mode="Markdown"
                    )
                    return

                # ── Settings: Delete Account (execute) ────────────────────────
                if data == "SETTINGS_DELETE_DO":
                    chat_id = call.message.chat.id
                    self.storage.delete_subscriber(chat_id)
                    # Clear all in-memory state for this user
                    self._setup_state.pop(chat_id, None)
                    self._user_is_existing.pop(chat_id, None)
                    self._known_users.discard(chat_id)
                    self._user_cache.pop(chat_id, None)
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=(
                            "😢 **Sorry to see you go!**\n\n"
                            "Your account has been deleted.\n\n"
                            "If you want to come back, just send /start and we'll get you set up again. 🙏"
                        ),
                        parse_mode="Markdown"
                    )
                    return

                # ── Settings: Back to settings menu ───────────────────────────
                if data == "SETTINGS_BACK":
                    chat_id = call.message.chat.id
                    self._setup_state.pop(chat_id, None)
                    self.bot.clear_step_handler_by_chat_id(chat_id)
                    
                    if chat_id in self._user_cache:
                        sub = self._user_cache[chat_id]
                    else:
                        sub = self.storage.get_subscriber(chat_id)
                        if sub:
                            self._user_cache[chat_id] = sub
                            
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=self._build_settings_text(sub),
                        reply_markup=self._build_settings_keyboard(sub),
                        parse_mode="Markdown"
                    )
                    return

                parts = data.split(':')
                action = parts[0]
                
                if action == 'PB':
                    try:
                        page = int(parts[1])
                    except (ValueError, IndexError):
                        logger.warning(f"Malformed PB callback data '{data}' from user {call.message.chat.id}")
                        return
                    if not (0 <= page <= _MAX_BHAVANA_PAGE):
                        logger.warning(f"Out-of-bounds page {page} in PB from user {call.message.chat.id}")
                        return
                    chat_id = call.message.chat.id
                    is_editing = bool(self._setup_state.get(chat_id, {}).get('editing'))
                    is_existing = self._user_is_existing.get(chat_id, False)
                    cancel_cb = "SETTINGS_BACK" if is_editing else "CANCEL"
                    msg_text = "Please select your **Bhavana**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(page=page, show_cancel=is_existing or is_editing, cancel_callback=cancel_cb),
                        parse_mode="Markdown"
                    )

                elif action == 'B':
                    try:
                        bhav_idx = int(parts[1])
                    except (ValueError, IndexError):
                        logger.warning(f"Malformed B callback data '{data}' from user {call.message.chat.id}")
                        return
                    if not (0 <= bhav_idx < len(BHAVANAS_LIST)):
                        logger.warning(f"Invalid bhavana index {bhav_idx} from user {call.message.chat.id}")
                        return
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    chat_id = call.message.chat.id
                    is_editing = bool(self._setup_state.get(chat_id, {}).get('editing'))
                    is_existing = self._user_is_existing.get(chat_id, False)
                    
                    cancel_cb = "SETTINGS_BACK" if is_editing else "CANCEL"
                    back_cb = "SETTING_BHAVANA" if is_editing else "START"
                    msg_text = f"Bhavana: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(
                            bhav_idx, 
                            page=0, 
                            show_cancel=is_existing or is_editing, 
                            cancel_callback=cancel_cb, 
                            back_callback=back_cb
                        ),
                        parse_mode="Markdown"
                    )

                elif action == 'PD':
                    try:
                        bhav_idx = int(parts[1])
                        page = int(parts[2])
                    except (ValueError, IndexError):
                        logger.warning(f"Malformed PD callback data '{data}' from user {call.message.chat.id}")
                        return
                    if not (0 <= bhav_idx < len(BHAVANAS_LIST)):
                        logger.warning(f"Invalid bhavana index {bhav_idx} from user {call.message.chat.id}")
                        return
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    max_dept_page = (len(BHAVANA_DEPARTMENTS_MAP.get(bhav_name, [])) - 1) // ITEMS_PER_PAGE
                    if not (0 <= page <= max(0, max_dept_page)):
                        logger.warning(f"Out-of-bounds page {page} in PD from user {call.message.chat.id}")
                        return
                    chat_id = call.message.chat.id
                    is_editing = bool(self._setup_state.get(chat_id, {}).get('editing'))
                    is_existing = self._user_is_existing.get(chat_id, False)
                    cancel_cb = "SETTINGS_BACK" if is_editing else "CANCEL"
                    back_cb = "SETTING_BHAVANA" if is_editing else "START"
                    msg_text = f"Bhavana: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(
                            bhav_idx, 
                            page=page, 
                            show_cancel=is_existing or is_editing, 
                            cancel_callback=cancel_cb, 
                            back_callback=back_cb
                        ),
                        parse_mode="Markdown"
                    )

                elif action == 'D':
                    try:
                        bhav_idx = int(parts[1])
                        dept_idx = int(parts[2])
                    except (ValueError, IndexError):
                        logger.warning(f"Malformed D callback data '{data}' from user {call.message.chat.id}")
                        return
                    
                    if not (0 <= bhav_idx < len(BHAVANAS_LIST)):
                        logger.warning(f"Invalid bhavana index {bhav_idx} from user {call.message.chat.id}")
                        return
                    
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    depts = BHAVANA_DEPARTMENTS_MAP.get(bhav_name, [])
                    if dept_idx != -1 and not (0 <= dept_idx < len(depts)):
                        logger.warning(f"Invalid department index {dept_idx} from user {call.message.chat.id}")
                        return

                    if dept_idx == -1:
                        dept_name = "All"
                    else:
                        dept_name = depts[dept_idx]
                    
                    chat_id = call.message.chat.id
                    state = self._setup_state.get(chat_id, {})
                    editing_mode = state.get('editing')

                    if editing_mode in ('bhavana', 'department'):
                        # Updating existing preferences directly from Settings
                        updates = {'department': dept_name}
                        if editing_mode == 'bhavana':
                            updates['bhavana'] = bhav_name
                        
                        success = self.storage.update_subscriber(chat_id, updates)
                        self._setup_state.pop(chat_id, None)
                        
                        if not success:
                            self.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=call.message.message_id,
                                text="❌ Oops! We had a problem saving your selection. Please try /settings again. 🙏",
                                parse_mode="Markdown"
                            )
                            return

                        sub = self._user_cache.get(chat_id) or self.storage.get_subscriber(chat_id) or {}
                        sub.update(updates)
                        self._user_cache[chat_id] = sub
                        self._user_is_existing[chat_id] = True

                        msg_text = (
                            f"✅ **Preferences Updated!**\n\n"
                            + self._build_settings_text(sub)
                        )
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text=msg_text,
                            reply_markup=self._build_settings_keyboard(sub),
                            parse_mode="Markdown"
                        )
                        return

                    # Normal onboarding flow (/start)
                    existing_name = state.get('name')
                    self._setup_state[chat_id] = {
                        'bhavana': bhav_name,
                        'department': dept_name,
                        'name': existing_name
                    }
                    
                    if not existing_name:
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text=f"Bhavana: {bhav_name}\nDepartment: {dept_name}",
                            parse_mode="Markdown"
                        )
                        msg = self.bot.send_message(
                            chat_id,
                            "Almost done! Please enter your **Name**:",
                            reply_markup=ForceReply(selective=True),
                            parse_mode="Markdown"
                        )
                        self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                    else:
                        success = self.storage.upsert_subscriber(
                            chat_id=chat_id,
                            bhavana=bhav_name,
                            department=dept_name,
                            name=existing_name,
                            receive_general_notices=True
                        )
                        if not success:
                            self.bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=call.message.message_id,
                                text="❌ Oops! We had a small hiccup while saving your selection. Please type /start to try again. 🙏",
                                parse_mode="Markdown"
                            )
                            return
                            
                        self._user_is_existing[chat_id] = True
                        self._setup_state.pop(chat_id, None)
                        sub = {
                            'telegram_chat_id': chat_id,
                            'bhavana': bhav_name,
                            'department': dept_name,
                            'name': existing_name,
                            'receive_general_notices': True
                        }
                        self._user_cache[chat_id] = sub
                        
                        safe_name = _escape_markdown(existing_name)
                        msg_text = (
                            f"✅ **Subscription Updated!**\n\n"
                            f"**{safe_name}**, you will now receive targeted notices for:\n"
                            f"🏛️ Bhavana: {bhav_name}\n"
                            f"📚 Department: {dept_name}\n"
                            f"📢 General Notices: ✅ Enabled\n\n"
                            f"_(Use /settings to change preferences at any time)_"
                        )
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text=msg_text,
                            parse_mode="Markdown"
                        )

            except Exception:
                logger.exception("Callback query error")