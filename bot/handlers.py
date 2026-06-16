import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import logging
import time
from functools import wraps

from bot.constants import ACADEMIC_LEVELS, BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP

logger = logging.getLogger(__name__)

ITEMS_PER_PAGE = 6

class BotHandlers:
    def __init__(self, bot, storage):
        self.bot = bot
        self.storage = storage
        self.setup_commands()

    def ensure_user(self, func):
        @wraps(func)
        def wrapper(message):
            user_id = message.chat.id
            username = message.from_user.username
            if self.storage.add_user(user_id, username):
                logger.info(f"New user {user_id} added from {func.__name__}.")
            return func(message)
        return wrapper

    def _build_level_keyboard(self):
        markup = InlineKeyboardMarkup(row_width=2)
        buttons = []
        for idx, lvl in enumerate(ACADEMIC_LEVELS):
            # Payload: L:{level_idx}
            buttons.append(InlineKeyboardButton(lvl, callback_data=f"L:{idx}"))
        markup.add(*buttons)
        return markup

    def _build_bhavana_keyboard(self, lvl_idx, page=0):
        markup = InlineKeyboardMarkup(row_width=1)
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        
        for idx in range(start_idx, min(end_idx, len(BHAVANAS_LIST))):
            bhavana = BHAVANAS_LIST[idx]
            # Payload: B:{lvl_idx}:{bhav_idx}
            markup.add(InlineKeyboardButton(bhavana, callback_data=f"B:{lvl_idx}:{idx}"))
            
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◄ Prev", callback_data=f"PB:{lvl_idx}:{page-1}"))
        if end_idx < len(BHAVANAS_LIST):
            nav_buttons.append(InlineKeyboardButton("Next ►", callback_data=f"PB:{lvl_idx}:{page+1}"))
            
        if nav_buttons:
            markup.add(*nav_buttons)
            
        # Add a back button to levels
        markup.add(InlineKeyboardButton("🔙 Back to Levels", callback_data="START"))
        return markup

    def _build_dept_keyboard(self, lvl_idx, bhav_idx, page=0):
        markup = InlineKeyboardMarkup(row_width=1)
        bhavana_name = BHAVANAS_LIST[bhav_idx]
        depts = BHAVANA_DEPARTMENTS_MAP[bhavana_name]
        
        start_idx = page * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        
        if page == 0:
            markup.add(InlineKeyboardButton("🌟 All (Entire Bhavana)", callback_data=f"D:{lvl_idx}:{bhav_idx}:-1"))
        
        for idx in range(start_idx, min(end_idx, len(depts))):
            dept = depts[idx]
            # Payload: D:{lvl_idx}:{bhav_idx}:{dept_idx}
            markup.add(InlineKeyboardButton(dept, callback_data=f"D:{lvl_idx}:{bhav_idx}:{idx}"))
            
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("◄ Prev", callback_data=f"PD:{lvl_idx}:{bhav_idx}:{page-1}"))
        if end_idx < len(depts):
            nav_buttons.append(InlineKeyboardButton("Next ►", callback_data=f"PD:{lvl_idx}:{bhav_idx}:{page+1}"))
            
        if nav_buttons:
            markup.add(*nav_buttons)
            
        # Add back button to bhavanas
        markup.add(InlineKeyboardButton("🔙 Back to Institutes", callback_data=f"L:{lvl_idx}"))
        return markup

    def setup_commands(self):
        @self.bot.message_handler(commands=['start', 'settings'])
        @self.ensure_user
        def start_command(message):
            msg_text = "👋 Welcome to the Visva-Bharati Notice Bot!\n\nPlease configure your subscription by selecting your **Academic Level**:"
            self.bot.send_message(
                message.chat.id, 
                msg_text, 
                reply_markup=self._build_level_keyboard(),
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

        @self.bot.message_handler(commands=['help'])
        @self.ensure_user
        def help_command(message):
            help_text = """
            Visva-Bharati Notice Bot Commands:
            /start - Setup notice alerts
            /settings - Reconfigure your subscription
            /status - Check current bot status
            /ping - Ping the bot
            /help - Display this help message
            """
            self.bot.reply_to(message, help_text)

        # CALLBACK QUERY HANDLERS
        @self.bot.callback_query_handler(func=lambda call: True)
        def callback_query(call):
            try:
                self.bot.answer_callback_query(call.id)
                data = call.data
                
                if data == "START":
                    msg_text = "Please select your **Academic Level**:"
                    self.bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_level_keyboard(),
                        parse_mode="Markdown"
                    )
                    return

                parts = data.split(':')
                action = parts[0]
                
                if action == 'L':
                    lvl_idx = int(parts[1])
                    lvl_name = ACADEMIC_LEVELS[lvl_idx]
                    msg_text = f"Level: {lvl_name}\n\nPlease select your **Institute (Bhavana)**:"
                    self.bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(lvl_idx, page=0),
                        parse_mode="Markdown"
                    )
                
                elif action == 'PB':
                    lvl_idx = int(parts[1])
                    page = int(parts[2])
                    lvl_name = ACADEMIC_LEVELS[lvl_idx]
                    msg_text = f"Level: {lvl_name}\n\nPlease select your **Institute (Bhavana)**:"
                    self.bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_bhavana_keyboard(lvl_idx, page=page),
                        parse_mode="Markdown"
                    )

                elif action == 'B':
                    lvl_idx = int(parts[1])
                    bhav_idx = int(parts[2])
                    lvl_name = ACADEMIC_LEVELS[lvl_idx]
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    msg_text = f"Level: {lvl_name}\nInstitute: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(lvl_idx, bhav_idx, page=0),
                        parse_mode="Markdown"
                    )

                elif action == 'PD':
                    lvl_idx = int(parts[1])
                    bhav_idx = int(parts[2])
                    page = int(parts[3])
                    lvl_name = ACADEMIC_LEVELS[lvl_idx]
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    msg_text = f"Level: {lvl_name}\nInstitute: {bhav_name}\n\nPlease select your **Department/Centre**:"
                    self.bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        reply_markup=self._build_dept_keyboard(lvl_idx, bhav_idx, page=page),
                        parse_mode="Markdown"
                    )

                elif action == 'D':
                    lvl_idx = int(parts[1])
                    bhav_idx = int(parts[2])
                    dept_idx = int(parts[3])
                    
                    lvl_name = ACADEMIC_LEVELS[lvl_idx]
                    bhav_name = BHAVANAS_LIST[bhav_idx]
                    if dept_idx == -1:
                        dept_name = "All"
                    else:
                        dept_name = BHAVANA_DEPARTMENTS_MAP[bhav_name][dept_idx]
                    
                    # Finalize selection
                    success = self.storage.upsert_subscriber(
                        chat_id=call.message.chat.id,
                        level=lvl_name,
                        bhavana=bhav_name,
                        department=dept_name
                    )
                    
                    if success:
                        msg_text = (
                            f"✅ **Subscription Confirmed!**\n\n"
                            f"You will now receive targeted notices for:\n"
                            f"🎓 Level: {lvl_name}\n"
                            f"🏛️ Institute: {bhav_name}\n"
                            f"📚 Department: {dept_name}\n\n"
                            f"_(Use /settings to change this at any time)_"
                        )
                    else:
                        msg_text = "❌ Failed to save your subscription. Please try again later."
                        
                    self.bot.edit_message_text(
                        chat_id=call.message.chat.id,
                        message_id=call.message.message_id,
                        text=msg_text,
                        parse_mode="Markdown"
                    )

            except Exception as e:
                logger.error(f"Callback query error: {e}")