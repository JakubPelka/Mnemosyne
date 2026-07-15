import re
import hashlib

def mask_private_text(text: str) -> str:
    if not text:
        return ""
    # Mask emails
    text = re.sub(r'[\w\.-]+@[\w\.-]+', '[EMAIL]', text)
    # Mask urls
    text = re.sub(r'https?://[^\s]+', '[URL]', text)
    # Mask paths
    text = re.sub(r'(?:/[a-zA-Z0-9_\-\.]+)+', '[PATH]', text)
    return text

def safe_hash(text: str) -> str:
    if not text: return ""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def sanitize_for_log(data: dict) -> dict:
    """Recursively removes or hashes sensitive fields before logging."""
    safe_data = {}
    for k, v in data.items():
        if 'text' in k.lower() or 'content' in k.lower():
            safe_data[k] = "[REDACTED]"
        elif isinstance(v, dict):
            safe_data[k] = sanitize_for_log(v)
        elif isinstance(v, list):
            safe_data[k] = [sanitize_for_log(i) if isinstance(i, dict) else i for i in v]
        else:
            safe_data[k] = v
    return safe_data
