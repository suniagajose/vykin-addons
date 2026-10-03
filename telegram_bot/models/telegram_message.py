import logging
from datetime import timedelta

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class TelegramMessage(models.Model):
    """Inbound/outbound message history and delivery tracking."""

    _name = "telegram.message"
    _description = "Telegram Message"
    _order = "id desc"
    # display_name is a computed preview; searching it searches the content.
    _rec_names_search = ["text", "caption"]

    # Threshold for auto-vacuum (days)
    _ACTIVE_THRESHOLD_DAYS = 15

    chat_id = fields.Many2one(
        comodel_name="telegram.chat",
        index=True,
        ondelete="cascade",
        help="Chat session this message belongs to",
    )
    bot_id = fields.Many2one(
        comodel_name="telegram.bot",
        required=True,
        index=True,
        help="Bot that sent or received this message",
    )
    parent_id = fields.Many2one(
        comodel_name="telegram.message",
        string="Reply To",
        ondelete="set null",
        help="Parent message if this is a reply",
    )

    msg_uid = fields.Char(
        string="Telegram Message ID",
        index=True,
        help="Unique message ID from Telegram API",
    )

    text = fields.Text(
        help="Full text content of the message",
    )
    command = fields.Char(
        help="Command name if message is a command (e.g., /start)",
    )

    message_type = fields.Selection(
        selection=[
            ("outbound", "Outbound"),
            ("inbound", "Inbound"),
        ],
        string="Type",
        required=True,
        default="outbound",
        help="Direction of message flow",
    )
    state = fields.Selection(
        selection=[
            ("outgoing", "In Queue"),
            ("sent", "Sent"),
            ("error", "Failed"),
            ("received", "Received"),
        ],
        string="Status",
        default="outgoing",
        help="Delivery status of the message",
    )
    error_message = fields.Char(
        help="Error details if message failed to send",
    )

    attachment_ids = fields.Many2many(
        comodel_name="ir.attachment",
        relation="telegram_message_attachment_rel",
        column1="message_id",
        column2="attachment_id",
        string="Attachments",
        help="Media files attached to this message (photos, videos, audio, etc.)",
    )
    media_type = fields.Selection(
        selection=[
            ("text", "Text"),
            ("photo", "Photo"),
            ("video", "Video"),
            ("audio", "Audio"),
            ("voice", "Voice Note"),
            ("document", "Document"),
        ],
        default="text",
        help="Type of media content in this message",
    )
    caption = fields.Text(
        help="Caption text for media messages",
    )

    # Telegram numbers messages per chat, so the same ID repeats across chats.
    _msg_uid_uniq = models.UniqueIndex(
        "(chat_id, msg_uid) WHERE msg_uid IS NOT NULL",
        "Telegram message ID must be unique per chat!",
    )

    @api.depends("text", "caption", "message_type", "media_type")
    def _compute_display_name(self):
        """Compute the display name with a message preview and media icon."""
        # Media type icons
        media_icons = {
            "text": "",
            "photo": "📷 ",
            "video": "🎥 ",
            "audio": "🎵 ",
            "voice": "🎤 ",
            "document": "📄 ",
        }

        for message in self:
            # Get media icon
            icon = media_icons.get(message.media_type, "")

            # Determine content for preview
            content = message.caption or message.text

            if content:
                preview = content[:50]
                if len(content) > 50:
                    preview += "..."
                message.display_name = f"{icon}[{message.message_type}] {preview}"
            # No text/caption - just show media type
            elif message.media_type and message.media_type != "text":
                media_label = dict(
                    message._fields["media_type"]._description_selection(message.env)
                ).get(message.media_type, message.media_type)
                message.display_name = f"{icon}[{message.message_type}] {media_label}"
            else:
                message.display_name = f"[{message.message_type}] Message {message.id}"

    @api.autovacuum
    def _gc_telegram_messages(self):
        """Auto-vacuum old delivered messages to curb database bloat.

        Removes ``sent``/``received`` messages older than
        ``_ACTIVE_THRESHOLD_DAYS`` days, keeping ``outgoing`` and ``error`` ones
        for debugging.

        :return: number of messages removed.
        :rtype: int
        """
        threshold_date = fields.Datetime.now() - timedelta(
            days=self._ACTIVE_THRESHOLD_DAYS
        )

        # Remove old successfully delivered messages
        # Keep 'outgoing' (pending) and 'error' (for debugging)
        old_messages = self.search(
            [
                ("create_date", "<", threshold_date),
                (
                    "state",
                    "in",
                    ["sent", "received"],
                ),  # Only successful messages
            ],
        )

        count = len(old_messages)
        if count > 0:
            _logger.info(
                "Auto-vacuum: Removing %s old telegram messages (>%s days)",
                count,
                self._ACTIVE_THRESHOLD_DAYS,
            )
            old_messages.unlink()
        else:
            _logger.debug("Auto-vacuum: No old messages to remove")

        return count
