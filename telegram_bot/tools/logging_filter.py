"""Logging filter to mask Telegram bot tokens in log output."""

import logging
import re

# A bot token is "<bot id>:<secret>". Bot IDs have grown past 10 digits, so the
# ID part is open-ended.
_TOKEN_PATTERN = re.compile(r"(\d{6,}):([A-Za-z0-9_-]{30,50})")

# HTTP logs: the webhook URL contains the token. In Odoo 20 the access log is
# emitted by "odoo.http.server", and a logger's filters do not apply to its
# children, so each logger is listed. This module's own code never logs a
# token (see TelegramClient), so its loggers need no filter.
_FILTERED_LOGGERS = ("odoo.http", "odoo.http.server")


def _mask_token(match):
    """Replace a token with a masked version showing the bot ID and last 4 chars."""
    return f"{match.group(1)}:***...{match.group(2)[-4:]}"


def mask_tokens(text):
    """Return ``text`` with every bot token masked.

    :param str text: text that may contain bot tokens
    :rtype: str
    """
    return _TOKEN_PATTERN.sub(_mask_token, text)


class TelegramTokenFilter(logging.Filter):
    """Logging filter that masks Telegram bot tokens in log messages.

    ``123456789:ABCdefGHIjklMNOpqrSTUvwxYZ0123456789`` becomes
    ``123456789:***...6789``.
    """

    def filter(self, record):
        """Mask tokens in the message, then let the record through.

        Odoo's formatter prints ``colored_message`` when the record carries it
        (the HTTP access log sets it, even when logging to a file), so it is
        masked too.

        :param record: the log record to filter
        :return: always True
        :rtype: bool
        """
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 — a logging filter must never raise
            return True
        masked = mask_tokens(message)
        if masked != message:
            record.msg = masked
            record.args = None
        colored = getattr(record, "colored_message", None)
        if isinstance(colored, str):
            record.colored_message = mask_tokens(colored)
        return True


def install_token_filter():
    """Install the token filter on the HTTP loggers. Called once at module load."""
    token_filter = TelegramTokenFilter()
    for name in _FILTERED_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, TelegramTokenFilter) for f in logger.filters):
            logger.addFilter(token_filter)
