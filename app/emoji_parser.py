import re
from typing import List, Tuple, Optional, Any, Dict
from telethon.extensions import html
from telethon.tl import types

def parse_html_to_message_and_entities(html_content: str) -> Tuple[str, List[types.TypeMessageEntity]]:
    """
    Parses HTML string into plain text and Telethon message entities,
    including <tg-emoji emoji-id="12345">🔥</tg-emoji>, <b>, <i>, <code>, <a>, etc.
    """
    if not html_content:
        return "", []
    try:
        text, entities = html.parse(html_content)
        return text, entities or []
    except Exception as e:
        # Fallback to plain text if HTML parsing encounters syntax issues
        return html_content, []

def unparse_message_and_entities_to_html(text: str, entities: Optional[List[Any]]) -> str:
    """
    Converts Telethon plain text and entities (especially MessageEntityCustomEmoji)
    back to full HTML with <tg-emoji emoji-id="..."> tags preserved.
    """
    if not text:
        return ""
    if not entities:
        return text
    try:
        return html.unparse(text, entities)
    except Exception:
        return text

def extract_custom_emojis(entities: Optional[List[Any]]) -> List[Dict[str, Any]]:
    """
    Extracts custom animated emoji details from entities.
    """
    custom_emojis = []
    if not entities:
        return custom_emojis
    for entity in entities:
        if isinstance(entity, types.MessageEntityCustomEmoji):
            custom_emojis.append({
                "document_id": str(entity.document_id),
                "offset": entity.offset,
                "length": entity.length
            })
    return custom_emojis

def sanitize_group_target(target: str) -> str:
    """
    Sanitizes group link / username into a clean identifier.
    Supports:
    - https://t.me/groupname -> groupname
    - @groupname -> groupname
    - https://t.me/+xyz -> invite link intact
    - https://t.me/joinchat/xyz -> invite link intact
    - -100123456789 -> integer ID or string
    """
    target = target.strip()
    if not target:
        return ""
    if target.startswith("https://t.me/joinchat/") or target.startswith("https://t.me/+"):
        return target
    if target.startswith("https://t.me/"):
        return target.replace("https://t.me/", "").replace("/", "").strip()
    if target.startswith("@"):
        return target[1:].strip()
    return target
