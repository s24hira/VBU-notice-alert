import re
from typing import Optional, Tuple
from bot.constants import BHAVANAS_LIST, BHAVANA_DEPARTMENTS_MAP

MAX_NAME_LENGTH = 100

def sanitize_name(name: str) -> str:
    """Strip control characters, newlines, and markdown injection characters."""
    if not isinstance(name, str):
        return ""
    # Strip control characters
    cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', name)
    # Remove Telegram markdown formatting characters
    cleaned = re.sub(r'[*_`\[\]()~<>#+\-=|{}.!]', '', cleaned)
    return cleaned.strip()

def validate_name(name: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validates a name string.
    Returns: (is_valid, sanitized_name, error_message)
    """
    if not isinstance(name, str):
        return False, None, "Name must be text."
    
    raw = name.strip()
    if not raw:
        return False, None, "Name cannot be empty."
    
    if raw.startswith('/'):
        return False, None, "Name cannot start with a command (/)."
        
    if len(raw) > MAX_NAME_LENGTH:
        return False, None, f"Name is too long (maximum {MAX_NAME_LENGTH} characters)."
        
    sanitized = sanitize_name(raw)
    if not sanitized or len(sanitized) < 1:
        return False, None, "Name contains only invalid or special characters. Please enter a valid name."
        
    return True, sanitized, None

def validate_bhavana(bhavana: str) -> bool:
    """Ensure bhavana exists in predefined allowed list."""
    return isinstance(bhavana, str) and bhavana in BHAVANA_DEPARTMENTS_MAP

def validate_department(bhavana: str, department: str) -> bool:
    """Ensure department is valid for the given bhavana, or is 'All'."""
    if not validate_bhavana(bhavana) or not isinstance(department, str):
        return False
    if department == "All":
        return True
    return department in BHAVANA_DEPARTMENTS_MAP.get(bhavana, [])

def validate_chat_id(chat_id: int) -> bool:
    """Ensure chat_id is a non-zero integer."""
    return isinstance(chat_id, int) and chat_id != 0

def validate_general_notices(val) -> bool:
    """Ensure receive_general_notices is a boolean."""
    return isinstance(val, bool)
