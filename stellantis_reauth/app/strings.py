"""Notification texts: built-in defaults, overridable from the add-on options."""

import logging

_LOGGER = logging.getLogger(__name__)

DEFAULTS = {
    "title": "Stellantis login",
    "renewed": "{name}: login renewed automatically (no SMS).",
    "renewed_manual": "{name}: login renewed on request (no SMS).",
    "renewed_sms": "{name}: login renewed with the SMS code.",
    "sms_prompt": "{name}: Stellantis has sent an SMS code. Reply to this notification with the code to renew the login.",
    "sms_action": "Enter SMS code",
    "sms_invalid": "That was not a valid code. Reply with the digits from the SMS.",
    "manual": "{name} needs to log in again and it can't be done automatically: {reason}. Finish it under Settings → Devices & services.",
    "failed": "{name}: automatic login failed: {error}",
    "reason_no_account": "no matching account in the add-on configuration",
    "reason_rate_limited": "an SMS reauth was already attempted in the last 6 hours",
    "reason_no_sms": "remote commands need an SMS code, but no PIN or phone notification target is available",
}

# Placeholders each text is formatted with; any other placeholder is an error.
PLACEHOLDERS = {
    "renewed": ("name",),
    "renewed_manual": ("name",),
    "renewed_sms": ("name",),
    "sms_prompt": ("name",),
    "manual": ("name", "reason"),
    "failed": ("name", "error"),
}


def build_messages(custom: dict | None) -> dict:
    """Defaults with the user's non-empty texts on top.

    A custom text that doesn't format with its placeholders (unknown
    placeholder, stray brace) falls back to the default, so a typo can't break
    notifications.
    """
    messages = dict(DEFAULTS)
    for key, text in (custom or {}).items():
        if key not in DEFAULTS or not text or not text.strip():
            continue
        try:
            text.format(**{p: p for p in PLACEHOLDERS.get(key, ())})
        except (KeyError, IndexError, ValueError, AttributeError) as err:
            allowed = ", ".join(f"{{{p}}}" for p in PLACEHOLDERS.get(key, ())) or "none"
            _LOGGER.warning("Custom text %s is invalid (%s); using the default. Allowed placeholders: %s", key, err, allowed)
            continue
        messages[key] = text
    return messages
