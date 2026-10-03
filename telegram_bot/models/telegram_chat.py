import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

# Conversational states this module owns: a chat sitting in one of them is
# mid-flow, so a command that would start another flow must not run.
_FLOW_STATES = ("onboarding", "awaiting_input")


class TelegramChat(models.Model):
    """Session of a Telegram chat with a bot, linked to an Odoo contact."""

    _name = "telegram.chat"
    _description = "Telegram Chat"
    _rec_name = "chat_id"

    chat_id = fields.Char(
        required=True,
        readonly=True,
        index=True,
    )
    chat_type = fields.Selection(
        selection=[
            ("private", "Private"),
            ("group", "Group"),
            ("supergroup", "Supergroup"),
            ("channel", "Channel"),
        ],
        readonly=True,
    )
    telegram_user_id = fields.Char(
        string="Telegram User ID",
        readonly=True,
        index=True,
        help="Numeric ID of the Telegram user who last wrote in this chat.",
    )
    telegram_username = fields.Char(
        readonly=True,
        help="@username of the last sender, for reference only: it is never used "
        "to link the chat.",
    )
    bot_id = fields.Many2one(
        comodel_name="telegram.bot",
        required=True,
        readonly=True,
        ondelete="cascade",
    )
    partner_id = fields.Many2one(
        comodel_name="res.partner",
        readonly=True,
        help="Contact whose Telegram User ID matches the sender. Set on each "
        "incoming update, only in private chats.",
    )
    state = fields.Selection(
        selection=[
            ("idle", "Idle"),
            ("onboarding", "Onboarding"),
            ("awaiting_input", "Awaiting Input"),
            ("processing", "Processing Command"),
            ("error", "Error State"),
            ("blocked", "Blocked"),
        ],
        required=True,
        default="idle",
        help="Current conversation state for managing multi-step interactions",
    )
    failed_command_count = fields.Integer(
        default=0,
        help="Number of consecutive failed commands. Auto-resets on success.",
    )
    last_error = fields.Char(
        help="Last error message for debugging.",
    )
    last_message_time = fields.Datetime(
        help="When the user last wrote in this chat.",
    )
    context_data = fields.Json(
        help="Conversation context for multi-step interactions.",
    )
    last_state_change = fields.Datetime(
        default=fields.Datetime.now,
        help="Track when state last changed for timeout detection",
    )

    _chat_id_bot_id_uniq = models.Constraint(
        "UNIQUE (chat_id, bot_id)",
        "A chat ID must be unique per bot!",
    )

    def write(self, vals):
        """Stamp ``last_state_change`` whenever the conversation state changes.

        :param dict vals: values to write
        :return: True if the write succeeded
        :rtype: bool
        """
        if "state" in vals and "last_state_change" not in vals:
            vals["last_state_change"] = fields.Datetime.now()
        return super().write(vals)

    @api.model
    def _find_or_create(self, bot, chat_data, from_data):
        """Return the chat of an incoming update, creating it if needed.

        Records the sender and links the chat to the contact whose Telegram User
        ID equals the sender's ID. Linking happens only in private chats: in a
        group, every member would act as whoever the chat is linked to. The link
        is recomputed on every update, so changing or clearing the ID on the
        contact takes effect on the next message.

        :param bot: ``telegram.bot`` record
        :param dict chat_data: ``chat`` object of the update
        :param dict from_data: ``from`` object of the update (the sender)
        :return: the chat record
        :rtype: telegram.chat
        """
        chat_id = str(chat_data.get("id"))
        chat_type = chat_data.get("type")
        user_id = str(from_data["id"]) if from_data.get("id") else False
        username = from_data.get("username") or False

        partner = self.env["res.partner"]
        if chat_type == "private" and user_id:
            partner = partner.search([("telegram_user_id", "=", user_id)], limit=1)

        vals = {
            "chat_type": chat_type,
            "telegram_user_id": user_id,
            "telegram_username": username,
            "partner_id": partner.id,
            # Any update (message or button) counts as activity, for the GC.
            "last_message_time": fields.Datetime.now(),
        }
        chat = self.search(
            [("chat_id", "=", chat_id), ("bot_id", "=", bot.id)], limit=1
        )
        if chat:
            chat.write(vals)
        else:
            chat = self.create({"chat_id": chat_id, "bot_id": bot.id, **vals})
            _logger.info("Created telegram.chat %s for bot '%s'", chat.id, bot.name)
        return chat

    def increment_failed_state(self, error_msg):
        """Increment the failed-command count and store the last error.

        :param str error_msg: error message to store
        """
        self.sudo().write(
            {
                "failed_command_count": self.failed_command_count + 1,
                "last_error": (error_msg[:255] if error_msg else False),
            },
        )

    def reset_failed_state(self):
        """Reset the failed-command count after a successful command."""
        if self.failed_command_count or self.last_error:
            self.sudo().write({"failed_command_count": 0, "last_error": False})

    def transition_to(self, new_state, context=None):
        """Transition the chat to ``new_state``, optionally storing context.

        :param str new_state: target state, must be a valid selection value
        :param dict context: optional context data to store
        :return: True if the transition succeeded
        :rtype: bool
        :raises ValidationError: if the state transition is not allowed
        """
        self.ensure_one()
        # An unknown state is rejected by the Selection field on write.
        invalid_transitions = {
            "blocked": ["processing", "awaiting_input"],
            "error": ["processing"],
        }
        if new_state in invalid_transitions.get(self.state, []):
            raise ValidationError(
                self.env._(
                    "Cannot transition from '%(from_state)s' to '%(to_state)s'",
                    from_state=self.state,
                    to_state=new_state,
                )
            )
        old_state = self.state
        # sudo: a technical bot operation; command handlers run as the linked
        # user, who only has read access on telegram.chat.
        self.sudo().write(
            {
                "state": new_state,
                "context_data": (context if context is not None else self.context_data),
            },
        )
        _logger.info(
            "Chat %s transitioned from '%s' to '%s'", self.id, old_state, new_state
        )
        return True

    def get_context(self, key, default=None):
        """Return the value stored in the context data for ``key``.

        :param str key: context key
        :param default: value returned when ``key`` is absent
        :return: the stored value, or ``default``
        """
        self.ensure_one()
        return (self.context_data or {}).get(key, default)

    def set_context(self, key, value):
        """Store ``value`` under ``key`` in the context data.

        :param str key: context key
        :param value: value to store, must be JSON-serializable
        """
        self.ensure_one()
        context = dict(self.context_data or {})
        context[key] = value
        self.sudo().write({"context_data": context})

    def _get_active_flow(self):
        """Return the flow currently occupying this chat, or None when free.

        Extension hook: a module that adds conversational states declares them
        here, so the flow guard can name what is in progress. Technical states
        (processing, error, blocked) are not flows: there is nothing for the
        user to finish or cancel in them.

        :return: dict with ``label`` and ``cancel_command``, or None
        :rtype: dict or None
        """
        self.ensure_one()
        if self.state in _FLOW_STATES:
            return {"label": "una operación", "cancel_command": None}
        return None

    def _reset_conversation_vals(self):
        """Build the ``write()`` values for a full conversation reset.

        Extension hook: modules that add flow-specific draft fields on
        ``telegram.chat`` override this to include them, so every reset also
        discards their drafts.

        :return: values resetting the chat to idle with no leftover context
        :rtype: dict
        """
        return {"state": "idle", "context_data": {}}

    def reset_conversation(self):
        """Reset every chat in ``self`` to idle, discarding all flow drafts."""
        # sudo: handlers run as the linked user, who cannot write on
        # telegram.chat; the chat session is a technical record owned by the bot.
        self.sudo().write(self._reset_conversation_vals())

    def clear_context(self):
        """Clear all conversation context and return to idle state."""
        self.ensure_one()
        self.reset_conversation()
        _logger.info("Chat %s context cleared and reset to idle", self.id)

    @api.model
    def _cron_reset_stale_states(self):
        """Reset chats stuck in a non-idle state for over an hour.

        :return: number of chats reset
        :rtype: int
        """
        stale_threshold = fields.Datetime.now() - timedelta(hours=1)
        stale_chats = self.search(
            [("state", "!=", "idle"), ("last_state_change", "<", stale_threshold)],
        )
        if stale_chats:
            _logger.warning("Resetting %s stale chats to idle", len(stale_chats))
            stale_chats.reset_conversation()
        return len(stale_chats)

    @api.autovacuum
    def _gc_telegram_chats(self):
        """Delete unlinked idle chats inactive for 90+ days and without messages.

        :return: number of chats removed
        :rtype: int
        """
        threshold_date = fields.Datetime.now() - timedelta(days=90)
        stale_chats = self.search(
            [
                ("partner_id", "=", False),
                ("state", "=", "idle"),
                "|",
                ("last_message_time", "<", threshold_date),
                ("last_message_time", "=", False),
            ],
        )
        with_messages = (
            self.env["telegram.message"]
            .search_fetch([("chat_id", "in", stale_chats.ids)], ["chat_id"])
            .chat_id
        )
        to_delete = stale_chats - with_messages
        if to_delete:
            _logger.info(
                "Auto-vacuum: removing %s old unlinked telegram chats", len(to_delete)
            )
            to_delete.unlink()
        return len(to_delete)
