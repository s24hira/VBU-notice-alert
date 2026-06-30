import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
import logging
import time
from functools import wraps

import re
from collections import defaultdict
from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 6

class BotHandlers:
    def __init__(self, bot, storage):
        self.bot = bot
        self.storage = storage
        self._rate_limit = defaultdict(list)
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
            username = message.from_user.username
            if self.storage.add_user(user_id, username):
                logger.info(f"New user {user_id} added from {func.__name__}.")
            return func(message)
        return wrapper

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
        name = re.sub(r'[*_`\[\]()~>#+\-=|{}.!]', '', name).strip()
        if not name:
            msg = self.bot.send_message(chat_id, "❌ Name contains only special characters. Please enter a valid name:", reply_markup=ForceReply(selective=True))
            self.bot.register_next_step_handler(msg, self._save_name_final_handler)
            return

        # Save name in the database
        sub = self.storage.get_subscriber(chat_id)
        bhavana = sub.get('bhavana') if sub else None
        department = sub.get('department') if sub else None
        
        success = self.storage.upsert_subscriber(chat_id, bhavana, department, name)
        
        if success:
            msg_text = (
                f"✅ **Subscription Confirmed!**\n\n"
                f"Welcome, **{name}**!\n"
                f"You will now receive targeted notices for:\n"
                f"🏛️ Bhavana: {bhavana}\n"
                f"📚 Department: {department}\n\n"
                f"_(Use /settings to change this at any time)_"
            )
        else:
            msg_text = "❌ Failed to save your subscription. Please try again later."
            
        self.bot.send_message(
            chat_id, 
            msg_text, 
            parse_mode="Markdown"
        )

    def setup_commands(self):
        @self.bot.message_handler(commands=['start'])
        @self.ensure_user
        def start_command(message):
            chat_id = message.chat.id
            sub = self.storage.get_subscriber(chat_id)
            if sub and sub.get('bhavana') and sub.get('department') and sub.get('name'):
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
            sub = self.storage.get_subscriber(chat_id)
            name_str = f" for **{sub['name']}**" if sub and sub.get('name') else ""
            msg_text = f"🔧 **Subscription Settings**{name_str}\n\nPlease select your **Institute (Bhavana)**:"
            is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
            self.bot.send_message(
                chat_id, 
                msg_text, 
                reply_markup=self._build_bhavana_keyboard(page=0, show_cancel=is_existing),
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
                self.bot.answer_callback_query(call.id)
                data = call.data
                
                if data == "START":
                    chat_id = call.message.chat.id
                    sub = self.storage.get_subscriber(chat_id)
                    is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
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
                    sub = self.storage.get_subscriber(chat_id)
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

                parts = data.split(':')
                action = parts[0]
                
                if action == 'PB':
                    page = int(parts[1])
                    chat_id = call.message.chat.id
                    sub = self.storage.get_subscriber(chat_id)
                    is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
                    msg_text = f"Please select your **Institute (Bhavana)**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(page=page, show_cancel=is_existing),
                        parse_mode="Markdown"
                    )

                elif action == 'B':
                    bhav_idx = int(parts[1])
                    if not (0 <= bhav_idx < len(BHAVANAS_LIST)):
                        logger.warning(f"Invalid bhavana index {bhav_idx} from user {call.message.chat.id}")
                        return
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    chat_id = call.message.chat.id
                    sub = self.storage.get_subscriber(chat_id)
                    is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
                    msg_text = f"Institute: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(bhav_idx, page=0, show_cancel=is_existing),
                        parse_mode="Markdown"
                    )

                elif action == 'PD':
                    bhav_idx = int(parts[1])
                    if not (0 <= bhav_idx < len(BHAVANAS_LIST)):
                        logger.warning(f"Invalid bhavana index {bhav_idx} from user {call.message.chat.id}")
                        return
                    page = int(parts[2])
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    chat_id = call.message.chat.id
                    sub = self.storage.get_subscriber(chat_id)
                    is_existing = bool(sub and sub.get('bhavana') and sub.get('department') and sub.get('name'))
                    msg_text = f"Institute: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(bhav_idx, page=page, show_cancel=is_existing),
                        parse_mode="Markdown"
                    )

                elif action == 'D':
                    bhav_idx = int(parts[1])
                    dept_idx = int(parts[2])
                    
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
                    sub = self.storage.get_subscriber(chat_id)
                    existing_name = sub.get('name') if sub else None

                    # Finalize selection for Bhavana and Dept
                    self.storage.upsert_subscriber(
                        chat_id=chat_id,
                        bhavana=bhav_name,
                        department=dept_name,
                        name=existing_name
                    )
                    
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

            except Exception as e:
                logger.error(f"Callback query error: {type(e).__name__}")