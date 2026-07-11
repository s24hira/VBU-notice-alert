import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import logging
import time
from functools import wraps

import re
from collections import defaultdict
from cachetools import TTLCache
from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP

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
        self._rate_limit = defaultdict(list)
        self._known_users = set()
        self._user_is_existing = {}
        self._setup_state = {}
        # Bounded TTL caches — prevent unbounded memory growth over time.
        # Entries expire after 2 hours; at most 10 000 concurrent users cached.
        self._user_cache = TTLCache(maxsize=10_000, ttl=7200)
        self.setup_commands()

    def _is_rate_limited(self, chat_id, max_calls=5, window_seconds=60):
        now = time.time()
        timestamps = self._rate_limit[chat_id]
        # Purge old entries
        self._rate_limit[chat_id] = [t for t in timestamps if now - t < window_seconds]
        if len(self._rate_limit[chat_id]) >= max_calls:
            return True
        self._rate_limit[chat_id].append(now)
        return False

    def ensure_user(self, func):
        @wraps(func)
        def wrapper(message):
            user_id = message.chat.id
            if self._is_rate_limited(user_id):
                logger.warning(f"User {user_id} rate limited.")
                return
            if user_id not in self._known_users:
                username = message.from_user.username
                if self.storage.add_user(user_id, username):
                    logger.info(f"New user {user_id} added from {func.__name__}.")
                self._known_users.add(user_id)
            return func(message)
        return wrapper

    def _build_settings_keyboard(self):
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("🔄 Reset Subscription", callback_data="SETTINGS_RESET"))
        markup.add(InlineKeyboardButton("🗑️ Delete Account", callback_data="SETTINGS_DELETE_CONFIRM"))
        return markup

    def _build_bhavana_keyboard(self, page=0, show_cancel=False):
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
            markup.add(InlineKeyboardButton("❌ Cancel", callback_data="CANCEL"))
        return markup

    def _build_dept_keyboard(self, bhav_idx, page=0, show_cancel=False):
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
            
        # Add back button to bhavanas
        markup.add(InlineKeyboardButton("🔙 Back to Institutes", callback_data="START"))
        if show_cancel:
            markup.add(InlineKeyboardButton("❌ Cancel", callback_data="CANCEL"))
        return markup

    def _save_name_final_handler(self, message):
        chat_id = message.chat.id
        
        try:
            if message.content_type != 'text':
                msg = self.bot.send_message(chat_id, "❌ Please send your name as a text message:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                return

            name = message.text.strip() if message.text else ""
            
            if not name:
                msg = self.bot.send_message(chat_id, "❌ Name cannot be empty. Please type your name:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                return

            if name.startswith('/'):
                self.bot.send_message(chat_id, "❌ Setup cancelled. You can type /start to try again.")
                return

            if len(name) > 100:
                msg = self.bot.send_message(chat_id, "❌ Name is too long (max 100 characters). Please try again:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                return

            # Sanitize for Telegram Markdown
            name = re.sub(r'[*_`\[\]()~<>#+\-=|{}.!]', '', name).strip()
            if not name:
                msg = self.bot.send_message(chat_id, "❌ Name contains only special characters. Please enter a valid name:", reply_markup=ForceReply(selective=True))
                self.bot.register_next_step_handler(msg, self._save_name_final_handler)
                return

            # Save name in the database
            setup_data = self._setup_state.get(chat_id)
            if not setup_data or not setup_data.get('bhavana') or not setup_data.get('department'):
                self.bot.send_message(
                    chat_id, 
                    "❌ Oops! We lost track of your Institute and Department selection. Please type /start to try again. 🙏"
                )
                return

            bhavana = setup_data.get('bhavana')
            department = setup_data.get('department')
            
            success = self.storage.upsert_subscriber(chat_id, bhavana, department, name)
            
            if success:
                self._setup_state.pop(chat_id, None)
                self._user_is_existing[chat_id] = True
                self._user_cache[chat_id] = {
                    'telegram_chat_id': chat_id,
                    'bhavana': bhavana,
                    'department': department,
                    'name': name
                }
                safe_name = _escape_markdown(name)
                msg_text = (
                    f"✅ **Subscription Confirmed!**\n\n"
                    f"Welcome, **{safe_name}**!\n"
                    f"You will now receive targeted notices for:\n"
                    f"🏛️ Bhavana: {bhavana}\n"
                    f"📚 Department: {department}\n\n"
                    f"_(Use /settings to change this at any time)_"
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
                msg_text = (
                    f"👋 You are already subscribed to VBU notice alerts!\n\n"
                    f"**Current Configuration:**\n"
                    f"👤 **Name:** {sub['name']}\n"
                    f"🏛️ **Bhavana:** {sub['bhavana']}\n"
                    f"📚 **Department:** {sub['department']}\n\n"
                    f"If you wish to change your configuration, please use /settings"
                )
                self.bot.send_message(chat_id, msg_text, parse_mode="Markdown")
                return
            
            # Start the setup flow directly if they are new or incomplete
            self._setup_state[chat_id] = {}
            msg_text = "👋 Welcome to the Visva-Bharati Notice Bot!\n\nPlease select your **Institute (Bhavana)** to begin:"
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

            name_str = f" for **{sub['name']}**" if sub and sub.get('name') else ""
            msg_text = (
                f"🔧 **Settings**{name_str}\n\n"
                f"What would you like to do?"
            )
            self.bot.send_message(
                chat_id,
                msg_text,
                reply_markup=self._build_settings_keyboard(),
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
                # Security: Only process callbacks in private chats where the
                # button presser is the chat owner. Prevents one user from
                # triggering account actions (e.g. deletion) on another user's behalf.
                if call.from_user.id != call.message.chat.id:
                    self.bot.answer_callback_query(call.id, "Unauthorized action.")
                    return

                self.bot.answer_callback_query(call.id)
                data = call.data
                
                if data == "START":
                    chat_id = call.message.chat.id
                    is_existing = self._user_is_existing.get(chat_id, False)
                    msg_text = "Please select your **Institute (Bhavana)**:"
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
                        msg_text = (
                            f"❌ **Settings change cancelled.**\n\n"
                            f"**Current Configuration:**\n"
                            f"👤 **Name:** {sub['name']}\n"
                            f"🏛️ **Bhavana:** {sub['bhavana']}\n"
                            f"📚 **Department:** {sub['department']}"
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

                # ── Settings: Reset ───────────────────────────────────────────
                if data == "SETTINGS_RESET":
                    chat_id = call.message.chat.id
                    # Clear name from state so the name-input step is triggered again
                    self._setup_state[chat_id] = {}
                    self._user_is_existing[chat_id] = True  # Keep cancel button available
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text="🔄 **Reset Subscription**\n\nPlease select your **Institute (Bhavana)**:",
                        reply_markup=self._build_bhavana_keyboard(page=0, show_cancel=True),
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
                    if chat_id in self._user_cache:
                        sub = self._user_cache[chat_id]
                    else:
                        sub = self.storage.get_subscriber(chat_id)
                        if sub:
                            self._user_cache[chat_id] = sub
                            
                    name_str = f" for **{sub['name']}**" if sub and sub.get('name') else ""
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=f"🔧 **Settings**{name_str}\n\nWhat would you like to do?",
                        reply_markup=self._build_settings_keyboard(),
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
                    is_existing = self._user_is_existing.get(chat_id, False)
                    msg_text = f"Please select your **Institute (Bhavana)**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(page=page, show_cancel=is_existing),
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
                    is_existing = self._user_is_existing.get(chat_id, False)
                    msg_text = f"Institute: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(bhav_idx, page=0, show_cancel=is_existing),
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
                    is_existing = self._user_is_existing.get(chat_id, False)
                    msg_text = f"Institute: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(bhav_idx, page=page, show_cancel=is_existing),
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
                    existing_name = state.get('name')

                    # Finalize selection for Bhavana and Dept in memory
                    self._setup_state[chat_id] = {
                        'bhavana': bhav_name,
                        'department': dept_name,
                        'name': existing_name
                    }
                    
                    if not existing_name:
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text=f"Institute: {bhav_name}\nDepartment: {dept_name}",
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
                        # Existing user updating settings -> save to DB now
                        success = self.storage.upsert_subscriber(
                            chat_id=chat_id,
                            bhavana=bhav_name,
                            department=dept_name,
                            name=existing_name
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
                        self._user_cache[chat_id] = {
                            'telegram_chat_id': chat_id,
                            'bhavana': bhav_name,
                            'department': dept_name,
                            'name': existing_name
                        }
                        
                        msg_text = (
                            f"✅ **Subscription Updated!**\n\n"
                            f"**{existing_name}**, you will now receive targeted notices for:\n"
                            f"🏛️ Bhavana: {bhav_name}\n"
                            f"📚 Department: {dept_name}\n\n"
                            f"_(Use /settings to change this at any time)_"
                        )
                        self.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=call.message.message_id,
                            text=msg_text,
                            parse_mode="Markdown"
                        )

            except Exception:
                logger.exception("Callback query error")