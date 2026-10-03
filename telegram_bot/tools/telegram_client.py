"""Thin client for the Telegram Bot API.

Pure API calls only: message tracking and state live in the ``telegram.bot`` model.
"""

import json
import logging

import requests

_logger = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
TIMEOUT = 15


class TelegramApiError(Exception):
    """A call to the Telegram Bot API could not be completed.

    Raised instead of the underlying ``requests`` error, whose message embeds the
    request URL and therefore the bot token.
    """


class TelegramClient:
    """Telegram Bot API client bound to one bot token."""

    def __init__(self, bot_token):
        """Initialize the client.

        :param str bot_token: Telegram bot token
        """
        self.bot_token = bot_token

    def _post(self, method, **kwargs):
        """Call a Bot API method and return its decoded JSON answer.

        Telegram answers errors (e.g. 400 Bad Request) with a JSON body carrying
        ``ok: false`` and a ``description``, so the HTTP status is not checked.

        :param str method: Bot API method name (e.g. ``sendMessage``)
        :param kwargs: passed to ``requests.post`` (``json``, ``data``, ``files``)
        :return: Telegram API response
        :rtype: dict
        :raises TelegramApiError: on a network error or a non-JSON answer
        """
        url = f"{API_URL}/bot{self.bot_token}/{method}"
        try:
            response = requests.post(url, timeout=TIMEOUT, **kwargs)
            return response.json()
        except (requests.RequestException, ValueError) as e:
            # Log only the exception type: the requests error text embeds the URL,
            # which carries the bot token. `from None` keeps it out of tracebacks.
            _logger.error("Telegram API call %s failed: %s", method, type(e).__name__)
            raise TelegramApiError(
                f"Telegram API call {method} failed: {type(e).__name__}"
            ) from None

    # MESSAGE METHODS

    def send_message(self, chat_id, text, parse_mode="Markdown", reply_markup=None):
        """Send a text message.

        :param str chat_id: target chat ID
        :param str text: message text
        :param str parse_mode: text parsing mode ('Markdown', 'HTML', or None)
        :param dict reply_markup: optional keyboard markup
        :return: Telegram API response
        :rtype: dict
        """
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        return self._post("sendMessage", json=payload)

    def send_document(self, chat_id, document, filename, caption=None):
        """Send a document file.

        :param str chat_id: target chat ID
        :param bytes document: document binary content
        :param str filename: filename with extension
        :param str caption: optional caption text
        :return: Telegram API response
        :rtype: dict
        """
        data = {"chat_id": chat_id, "parse_mode": "Markdown"}
        if caption:
            data["caption"] = caption
        files = {"document": (filename, document)}
        return self._post("sendDocument", data=data, files=files)

    # FILE METHODS

    def get_file(self, file_id):
        """Get file information (including ``file_path``).

        :param str file_id: Telegram file ID
        :return: Telegram API response with file metadata
        :rtype: dict
        """
        return self._post("getFile", json={"file_id": file_id})

    def download_file(self, file_path):
        """Download file content from Telegram's servers.

        :param str file_path: file path from a :meth:`get_file` response
        :return: file binary content
        :rtype: bytes
        :raises TelegramApiError: on a network or HTTP error
        """
        url = f"{API_URL}/file/bot{self.bot_token}/{file_path}"
        try:
            response = requests.get(url, timeout=TIMEOUT)
            response.raise_for_status()
        except requests.RequestException as e:
            # Same as _post: the error text carries the token-bearing URL.
            _logger.error(
                "Failed to download file '%s': %s", file_path, type(e).__name__
            )
            raise TelegramApiError(
                f"Failed to download file: {type(e).__name__}"
            ) from None
        return response.content

    def get_file_content(self, file_id):
        """Get file content by ID (combines :meth:`get_file` and :meth:`download_file`).

        :param str file_id: Telegram file ID
        :return: file binary content
        :rtype: bytes
        :raises TelegramApiError: if the file info cannot be obtained
        """
        file_info = self.get_file(file_id)
        if not file_info or not file_info.get("ok"):
            raise TelegramApiError(f"Could not get file info for file_id: {file_id}")
        return self.download_file(file_info["result"]["file_path"])

    # CALLBACK METHODS

    def answer_callback_query(self, callback_query_id, text=None, show_alert=False):
        """Answer a callback query from an inline keyboard.

        :param str callback_query_id: identifier of the query to answer
        :param str text: optional text to show the user (0-200 characters)
        :param bool show_alert: show an alert instead of a notification
        :return: Telegram API response
        :rtype: dict
        """
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        if show_alert:
            payload["show_alert"] = show_alert
        return self._post("answerCallbackQuery", json=payload)

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        """Replace the inline keyboard of an existing message.

        Passing ``reply_markup=None`` removes the keyboard.

        :param str chat_id: target chat ID
        :param int message_id: ID of the message to edit
        :param dict reply_markup: new inline keyboard markup, or None to clear
        :return: Telegram API response
        :rtype: dict
        """
        payload = {"chat_id": chat_id, "message_id": message_id}
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        return self._post("editMessageReplyMarkup", json=payload)

    # WEBHOOK METHODS

    def set_webhook(self, url, secret_token, allowed_updates=None):
        """Register the webhook URL and the secret Telegram sends back in each update.

        :param str url: public HTTPS URL of the webhook
        :param str secret_token: value Telegram sends in the
            ``X-Telegram-Bot-Api-Secret-Token`` header
        :param list allowed_updates: update types to receive
        :return: Telegram API response
        :rtype: dict
        """
        payload = {"url": url, "secret_token": secret_token}
        if allowed_updates is not None:
            payload["allowed_updates"] = allowed_updates
        return self._post("setWebhook", json=payload)
