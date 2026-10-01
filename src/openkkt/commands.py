"""Command admission only. Does not execute OS commands or send messages."""
import re


def parse_command(chat_id: str, row: dict, binding: dict, timestamp: int) -> str | None:
    # Binding must be explicitly configured after checking the self-chat ID and
    # account author ID. Category membership NEVER grants command permission.
    if (str(chat_id) != str(binding["chat_id"])
            or str(row["author_id"]) != str(binding["author_id"])
            or row["deleted"] or row["type"] != 1):
        return None
    age = timestamp - row["sent_at"]
    if not 0 <= age <= 300:
        return None
    text = row["text"].strip()
    name = re.escape(binding.get("name", "Assistant"))
    # Replies use a bracketed assistant name, which never matches this rule.
    match = re.match(r'^(?:"' + name + r'"|' + name + r')(?:\s+|\s*[:,]\s*)(.+)$', text,
                     flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match and match.group(1).strip() else None
