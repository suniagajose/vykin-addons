import hmac
import logging
import time
from functools import wraps
from typing import ClassVar

from odoo import http
from odoo.http import request

_logger = logging.getLogger(__name__)

# Callback-data namespace for the "you already have a flow running" notice.
_BUSY_CALLBACK_PREFIX = "tg_busy:"


def log_command_execution(func):
    """Log a command handler's name, user, duration, and success/failure.

    :param func: command handler function
    :rtype: callable
    """

    @wraps(func)
    def wrapper(self, bot, chat, args, partner=False, internal_user=False):
        command_name = func.__name__.replace("_handle_", "").replace("_command", "")
        user_info = (
            f"partner={partner.id if partner else 'None'}, "
            f"user={internal_user.login if internal_user else 'None'}"
        )

        _logger.info("Executing command '%s' for %s", command_name, user_info)
        start_time = time.time()

        try:
            result = func(self, bot, chat, args, partner, internal_user)
            duration = time.time() - start_time
            _logger.info(
                "Command '%s' completed successfully in %.2fs",
                command_name,
                duration,
            )
            return result

        except Exception:
            duration = time.time() - start_time
            _logger.exception(
                "Command '%s' failed after %.2fs",
                command_name,
                duration,
            )
            raise

    return wrapper


class TelegramController(http.Controller):
    """Base Telegram Bot Controller.

    This controller handles webhook requests from Telegram and dispatches
    commands to appropriate handlers. It implements a permission system
    with three levels: unlinked users, partners, and internal users.

    Child controllers can extend this to add domain-specific commands.
    """

    _command_handlers: ClassVar[dict[str, str]] = {
        "/start": "_handle_start_command",
        "/ayuda": "_handle_help_command",
        "/quiensoy": "_handle_whoami_command",
    }
    # Core commands every bot answers; any other command must also be enabled on
    # the bot (telegram.bot.command_ids). Frozen here, so a child controller
    # extending _command_handlers does not enable its commands on every bot.
    _core_cmds: ClassVar[frozenset[str]] = frozenset(_command_handlers)

    _unlinked_user_cmds: ClassVar[set[str]] = {"/start", "/ayuda"}
    _partner_added_cmds: ClassVar[set[str]] = {"/quiensoy"}
    _internal_user_added_cmds: ClassVar[set[str]] = set()
    # Commands that start a multi-step flow. A child controller registers its
    # own here so _dispatch_command can refuse to start a second flow over an
    # unfinished one, whichever module owns either side.
    _flow_starting_cmds: ClassVar[set[str]] = set()

    @http.route(
        "/telegram/webhook/<string:token>",
        type="http",
        auth="public",
        methods=["POST"],
        csrf=False,
        save_session=False,
    )
    def webhook(self, token, **kwargs):
        """Main webhook entry point for Telegram updates.

        Updates are processed synchronously (no queue, no threads). The response
        is always HTTP 200 so Telegram never retries a delivered update.

        :param str token: bot token from the URL path
        :rtype: odoo.http.Response
        """
        try:
            try:
                data = request.get_json_data()
            except ValueError:  # empty or malformed body
                return request.make_response("OK")

            # Find bot by token. The webhook endpoint is auth="public" (Telegram
            # hits it without an Odoo session), so find_by_token searches with
            # sudo. The token and the secret header (verified below) are the
            # only authentication signals.
            bot = request.env["telegram.bot"].find_by_token(token)
            if not bot:
                # Never log any part of the raw token — even a prefix leaks the
                # bot_id on api.telegram.org. Hash it for correlation instead.
                token_fp = bot._hash_token(token)[:12]
                _logger.warning(
                    "Invalid webhook token received (sha256 fingerprint=%s)",
                    token_fp,
                )
                return request.make_response("OK")

            # The secret is mandatory: a bot without one ignores every update
            # (fail closed). Constant-time comparison so response timing does
            # not leak the secret; empty operands are rejected before calling
            # compare_digest, which does not accept None.
            received_secret = request.httprequest.headers.get(
                "X-Telegram-Bot-Api-Secret-Token"
            )
            if (
                not bot.webhook_secret
                or not received_secret
                or not hmac.compare_digest(received_secret, bot.webhook_secret)
            ):
                _logger.warning("Invalid or missing secret for bot '%s'", bot.name)
                return request.make_response("OK")

            _logger.debug("Processing update for bot '%s': %s", bot.name, data)

            if "message" in data:
                self._dispatch_message(bot, data["message"])
            elif "callback_query" in data:
                self._dispatch_callback_query(bot, data["callback_query"])

            return request.make_response("OK")

        except Exception:
            _logger.exception("Webhook error")
            return request.make_response("OK")

    def _get_chat_and_user(self, bot, message_or_callback):
        """Extract chat, partner, and internal user from an incoming update.

        `bot.env` is the sudo environment inherited from the `find_by_token`
        lookup in the webhook handler, so every chat/message/partner read and
        write below runs as superuser: the endpoint is `auth="public"` and
        there is no user-facing permission boundary once the token and secret
        header have been validated. The partner is the one whose
        `telegram_user_id`, set by an administrator, equals the sender's numeric
        Telegram ID (see `telegram.chat._find_or_create`).

        :param bot: telegram.bot record (sudo env from the webhook lookup)
        :param dict message_or_callback: message or callback query payload
        :return: (chat, partner, internal_user)
        :rtype: tuple
        """
        is_callback = "data" in message_or_callback
        if is_callback:
            chat_data = message_or_callback.get("message", {}).get("chat", {})
        else:
            chat_data = message_or_callback.get("chat", {})
        from_data = message_or_callback.get("from", {})

        chat = bot.env["telegram.chat"]._find_or_create(bot, chat_data, from_data)

        partner = chat.partner_id
        # Only an internal user reaches the third permission level: a portal
        # user linked to the same contact stays at the contact level.
        internal_user = (
            partner.user_ids.filtered(lambda user: user._is_internal())[:1] or False
        )

        _logger.debug(
            "Chat %s: partner %s, internal user %s",
            chat.id,
            partner.id or "(none)",
            internal_user.id if internal_user else "(none)",
        )
        return chat, partner, internal_user

    def _env_as_user(self, bot, internal_user):
        """Return an environment acting as the linked internal user.

        The webhook request runs as the public user, who is also the
        transaction's origin user. Odoo falls back to that origin user when it
        writes relations to models that refuse sudo commands (res.users, via
        ``_allow_sudo_commands``), so ``bot.env(user=...)`` alone would write
        a task's assignees as public and fail. ``request.update_env`` moves the
        origin user too. Without a request (direct calls) there is none to move.

        :param bot: telegram.bot record
        :param internal_user: linked internal res.users record
        :return: environment of the internal user, without superuser mode
        :rtype: odoo.api.Environment
        """
        if request:
            request.update_env(user=internal_user)
            return request.env
        return bot.env(user=internal_user)

    def _sanitize_message_text(self, text):
        """Strip non-breaking spaces, collapse whitespace, trim trailing punctuation.

        :param str text: raw message text
        :rtype: str
        """
        if not text:
            return ""

        # Remove non-breaking spaces (common in copy-paste)
        text = text.replace("\xa0", " ")

        # Normalize whitespace
        text = " ".join(text.split())

        # Strip leading/trailing whitespace and punctuation
        return text.strip().rstrip(".!")

    def _dispatch_message(self, bot, message):
        """Dispatch an incoming message to the appropriate handler.

        :param bot: telegram.bot record
        :param dict message: message payload from Telegram
        """
        # Skip messages authored by a bot to avoid response loops.
        if message.get("from", {}).get("is_bot"):
            _logger.debug("Skipping message from bot user to prevent loop")
            return

        chat, partner, internal_user = self._get_chat_and_user(bot, message)

        # Create telegram.message record for incoming message. Uses savepoint
        # to isolate DB errors (like duplicate msg_uid) from the main
        # transaction; bot.env is sudo (see _get_chat_and_user docstring).
        message_text = message.get("text", "")
        msg_uid = str(message.get("message_id")) if message.get("message_id") else False
        command, args = False, []
        if message_text.startswith("/"):
            parts = self._sanitize_message_text(message_text).split()
            if parts:
                command, args = parts[0].split("@")[0], parts[1:]

        # Check if message already exists (deduplication). Telegram numbers
        # messages per chat, so the lookup is scoped to the chat.
        if msg_uid:
            existing = bot.env["telegram.message"].search(
                [("chat_id", "=", chat.id), ("msg_uid", "=", msg_uid)], limit=1
            )
            if existing:
                _logger.debug("Message %s already exists, skipping creation", msg_uid)
            else:
                try:
                    with bot.env.cr.savepoint():
                        bot.env["telegram.message"].create(
                            {
                                "bot_id": bot.id,
                                "chat_id": chat.id,
                                "text": message_text,
                                "message_type": "inbound",
                                "state": "received",
                                "msg_uid": msg_uid,
                                "command": command,
                            }
                        )
                except Exception as e:
                    # Savepoint rolled back, log but continue processing
                    _logger.warning(
                        "Failed to create message record (msg_uid=%s): %s",
                        msg_uid,
                        e,
                    )

        # A shared contact is handled specially (linking is manual, not by phone).
        if "contact" in message:
            self._handle_shared_contact(bot, chat, message, partner, internal_user)
            return

        if command:
            self._dispatch_command(bot, chat, command, args, partner, internal_user)
        # Flow to manage an answer that is not a command (e.g., a file)
        else:
            # This method can be overridden by child controllers
            self._handle_non_command_message(bot, chat, message, partner, internal_user)

    def _dispatch_callback_query(self, bot, callback_query):
        """Dispatch a callback query from an inline keyboard.

        :param bot: telegram.bot record
        :param dict callback_query: callback query payload from Telegram
        """
        chat, partner, internal_user = self._get_chat_and_user(bot, callback_query)
        # The busy-notice buttons are answered here, before the state-based
        # routing in _handle_callback_query: the chat is still in the state that
        # triggered the notice, so a child router that dispatches on state could
        # match this callback against a step it does not belong to.
        if (callback_query.get("data") or "").startswith(_BUSY_CALLBACK_PREFIX):
            self._handle_flow_busy_callback(
                bot, chat, callback_query, partner, internal_user
            )
            return
        # This method can be overridden by child controllers
        self._handle_callback_query(bot, chat, callback_query, partner, internal_user)

    def _dispatch_command(self, bot, chat, command, args, partner, internal_user):
        """Find and call the command handler allowed for the user's permission level.

        :param bot: telegram.bot record
        :param chat: telegram.chat record
        :param str command: command string (e.g. "/start")
        :param list args: command arguments
        :param partner: res.partner record or False
        :param internal_user: res.users record or False
        """

        handler_name = self._command_handlers.get(command)
        available_cmds = self._core_cmds | set(bot.command_ids.mapped("name"))
        if not handler_name or command not in available_cmds:
            bot.send_message(
                chat.chat_id,
                f"Comando desconocido: `{command}`. Escribe /ayuda para ver la lista.",
            )
            return

        handler_method = getattr(self, handler_name, None)
        if not handler_method:
            _logger.error("Handler '%s' not implemented in controller.", handler_name)
            return

        # Build hierarchically allowed commands
        allowed_cmds = self._unlinked_user_cmds.copy()
        if partner:
            allowed_cmds.update(self._partner_added_cmds)
        if internal_user:
            allowed_cmds.update(self._internal_user_added_cmds)

        # Centralized permission check
        if command not in allowed_cmds:
            if not partner:
                msg = (
                    f"Para usar el comando `{command}` tu cuenta de Telegram "
                    "debe estar vinculada. Escribe /start para ver tu ID de "
                    "Telegram y pide al administrador que lo vincule."
                )
            else:
                msg = f"No tienes permiso para usar el comando `{command}`."
            bot.send_message(chat.chat_id, msg)
            return

        # Centralized flow guard. Runs before the handler and writes nothing,
        # so the flow already in progress cannot be damaged by construction:
        # a start command used to overwrite the chat state and blank the other
        # flow's draft, orphaning it.
        if command in self._flow_starting_cmds:
            active_flow = chat._get_active_flow()
            if active_flow:
                self._send_flow_busy_notice(bot, chat, active_flow, command)
                return

        # An internal user's command runs with that user's access rights: the
        # records are re-bound to an env acting as the user (not superuser).
        handler_bot, handler_chat, handler_partner = bot, chat, partner
        if internal_user:
            env_as_user = self._env_as_user(bot, internal_user)
            handler_bot = bot.with_env(env_as_user)
            handler_chat = chat.with_env(env_as_user)
            handler_partner = partner.with_env(env_as_user)
        try:
            handler_method(
                handler_bot,
                handler_chat,
                args,
                partner=handler_partner or False,
                internal_user=internal_user,
            )
            chat.reset_failed_state()

        except Exception as e:
            _logger.error("Command handler '%s' failed: %s", command, e)

            chat.increment_failed_state(str(e))

            # Proactively point the user to /ayuda after 3 consecutive failures.
            if chat.failed_command_count >= 3:
                bot.send_message(
                    chat.chat_id,
                    (
                        "Parece que algo no está saliendo. Escribe /ayuda para ver "
                        "los comandos disponibles."
                    ),
                )
                chat.reset_failed_state()  # Reset after suggestion
            else:
                bot.send_message(
                    chat.chat_id,
                    "Ocurrió un error al procesar tu comando. Inténtalo de nuevo.",
                )

    # FLOW GUARD METHODS

    def _flow_guard_blocks(self, bot, chat, data=None):
        """Refuse a button-started flow when another flow owns the chat.

        :param bot: telegram.bot record
        :param chat: telegram.chat record
        :param str data: callback payload, or None
        :return: True when the callback was refused and must not run
        :rtype: bool
        """
        # Declared here so every child overriding it shares one signature and can
        # chain super(). Odoo merges all TelegramController subclasses into a
        # single class, so two children declaring incompatible arities would leave
        # one call site raising TypeError depending on discovery order. A child
        # that does not own `data` must delegate rather than answer for it.
        return False

    def _send_flow_busy_notice(
        self, bot, chat, active_flow, command=None, trigger="button"
    ):
        """Name the flow that owns the chat and offer a way out of it.

        :param bot: telegram.bot record
        :param chat: telegram.chat record
        :param dict active_flow: descriptor returned by ``_get_active_flow()``
        :param str command: the flow-starting command that was refused, or None
            when no command was involved
        :param str trigger: what the user did when *command* is None —
            ``"button"`` for an inline button, ``"message"`` for text or voice
        """
        md = bot.get_markdown_helpers()
        lines = [
            f"⚠️ Ya tienes {active_flow['label']} en proceso.",
            "",
        ]
        if command:
            lines.append(
                f"Termina lo que traes antes de usar {md['code'](command)}, "
                "o elige una opción:"
            )
        elif trigger == "message":
            # Nothing to press again: the user typed or spoke. Name the two real
            # ways out — answering the flow's last prompt, or the single discard
            # button below — instead of an action never taken.
            lines.append(
                "Termina lo que traes respondiendo el último mensaje que te "
                "mandé, o descártalo con el botón de abajo."
            )
        else:
            # A button started it: that keyboard stays in the chat, so the user
            # can simply press it again afterwards. That spares us replaying a
            # synthesized callback with a stale id.
            lines.append(
                "Descarta lo que traes o termínalo, y vuelve a picar el botón."
            )
        cancel_command = active_flow.get("cancel_command")
        if cancel_command:
            lines.append("")
            # No gendered article: the label can be masculine ("un registro de
            # pago") or feminine ("una solicitud").
            lines.append(
                f"También puedes escribir {md['code'](cancel_command)} para cancelar."
            )
        if command:
            # "Descartar" carries the refused command so the callback can start
            # it right away: without it the user has to remember which of the
            # three cancel commands applies to the flow that is blocking them.
            buttons = [
                [
                    {
                        "text": "▶️ Continuar con lo que traigo",
                        "callback_data": f"{_BUSY_CALLBACK_PREFIX}keep",
                    }
                ],
                [
                    {
                        "text": "🗑 Descartar y empezar de nuevo",
                        "callback_data": f"{_BUSY_CALLBACK_PREFIX}restart:{command}",
                    }
                ],
            ]
        else:
            # The trigger rides in the callback data so the discard reply can
            # point back at the right thing: a typed message has no button to
            # press again. Older keyboards still in a chat carry the bare
            # "discard" and keep the button wording.
            discard = "discard:message" if trigger == "message" else "discard"
            buttons = [
                [
                    {
                        "text": "🗑 Descartar lo que traigo",
                        "callback_data": f"{_BUSY_CALLBACK_PREFIX}{discard}",
                    }
                ]
            ]
        bot.send_message(
            chat.chat_id, "\n".join(lines), reply_markup={"inline_keyboard": buttons}
        )
        if command:
            refused = command
        elif trigger == "message":
            refused = "a plain message"
        else:
            refused = "an inline-button flow"
        _logger.info(
            "Chat %s refused '%s': flow already active in state '%s'",
            chat.id,
            refused,
            chat.state,
        )

    def _handle_flow_busy_callback(
        self, bot, chat, callback_query, partner, internal_user
    ):
        """Answer the busy-notice buttons: keep, discard, or discard and restart.

        :param bot: telegram.bot record
        :param chat: telegram.chat record
        :param dict callback_query: Telegram callback_query payload
        :param partner: res.partner record or False
        :param internal_user: res.users record or False
        """
        bot.answer_callback_query(callback_query.get("id"))
        # Drop the keyboard so the same notice cannot be acted on twice.
        message_id = (callback_query.get("message") or {}).get("message_id")
        if message_id:
            bot.edit_message_reply_markup(chat.chat_id, message_id, reply_markup=None)

        action = (callback_query.get("data") or "")[len(_BUSY_CALLBACK_PREFIX) :]
        if action == "keep":
            bot.send_message(
                chat.chat_id,
                (
                    "Va, sigue donde te quedaste. "
                    "Responde el último mensaje que te mandé."
                ),
            )
            return

        if action.startswith("discard"):
            # Nothing to re-dispatch either way, so clear the way and point the
            # user back at whatever they were doing when the flow refused them.
            # The notice carries the trigger in the callback data precisely for
            # this line: someone who typed has no button to press again, and a
            # keyboard already sitting in a chat sends the bare "discard", so
            # the button wording stays the default.
            chat.reset_conversation()
            if action == "discard:message":
                notice = (
                    "Listo, descarté lo que traías. "
                    "Vuelve a escribirme lo que necesitas."
                )
            else:
                notice = (
                    "Listo, descarté lo que traías. "
                    "Vuelve a picar el botón que querías."
                )
            bot.send_message(chat.chat_id, notice)
            return

        command = action.removeprefix("restart:")
        # callback_data is client-supplied, so only a command already registered
        # as flow-starting may be re-dispatched through this path.
        if not action.startswith("restart:") or command not in self._flow_starting_cmds:
            _logger.warning(
                "Chat %s sent an unknown flow-busy action '%s'", chat.id, action
            )
            return

        chat.reset_conversation()
        bot.send_message(
            chat.chat_id, "Listo, descarté lo anterior. Empezamos de nuevo."
        )
        self._dispatch_command(bot, chat, command, [], partner, internal_user)

    # OVERRIDABLE HANDLER METHODS

    def _handle_non_command_message(self, bot, chat, message, partner, internal_user):
        """Handle a non-command message; override in child controllers."""
        # A message arriving while a flow owns the chat is NOT an unknown
        # command: the user has an operation waiting on them, and answering "I
        # only understand commands" hides that. Reuse the busy notice the
        # command guard already sends, so the answer names what is in progress
        # and offers a way out. _get_active_flow is the hook every flow module
        # already declares, so a new flow is covered here for free.
        active_flow = chat._get_active_flow()
        if active_flow:
            self._send_flow_busy_notice(bot, chat, active_flow, trigger="message")
            return
        if not partner:
            # Flow modules only serve linked users: tell an unlinked one how to
            # get linked instead of pointing them at commands they cannot use.
            bot.send_message(
                chat.chat_id,
                "Tu cuenta de Telegram no está vinculada con Odoo. Escribe /start "
                "para ver tu ID de Telegram y pídele al administrador que lo vincule.",
            )
            return
        bot.send_message(
            chat.chat_id,
            ("Solo puedo procesar comandos. Escribe /ayuda para ver la lista."),
        )

    def _handle_callback_query(self, bot, chat, callback_query, partner, internal_user):
        """Handle a callback query; override in child controllers."""
        bot.send_message(
            chat.chat_id,
            "Recibí una acción, pero no sé cómo procesarla.",
        )

    # COMMAND HANDLER METHODS

    @log_command_execution
    def _handle_start_command(
        self, bot, chat, args, partner=False, internal_user=False
    ):
        """Handle the /start command.

        An unlinked user also gets their numeric Telegram ID: it is what the
        administrator sets on their contact to link the chat.
        """
        md = bot.get_markdown_helpers()
        welcome_message = (
            f"¡Bienvenido a {md['bold'](bot.name)}!\n"
            "Escribe /ayuda para ver qué puedo hacer."
        )
        if not partner and chat.telegram_user_id:
            welcome_message += (
                f"\n\nTu ID de Telegram es {md['code'](chat.telegram_user_id)}. "
                "Pásaselo al administrador para vincular tu cuenta con Odoo."
            )
        bot.send_message(chat.chat_id, welcome_message)

    def _get_help_sections(self, bot, partner, internal_user, chat=None):
        """Build the help sections for the /ayuda command.

        Child controllers should override this and call super() to append their
        own sections.

        :param bot: telegram.bot record
        :param partner: res.partner record or False
        :param internal_user: res.users record or False
        :param chat: telegram.chat record or None (for role-based filtering)
        :return: list of (section_title, [command_descriptions]) tuples
        :rtype: list
        """
        sections = []

        # General commands section
        general_cmds = []
        if partner:
            general_cmds.append("/quiensoy - Verifica tu identidad en Odoo.")
        general_cmds.append("/ayuda - Muestra este mensaje de ayuda.")

        sections.append(("Comandos Generales", general_cmds))

        return sections

    @log_command_execution
    def _handle_help_command(self, bot, chat, args, partner=False, internal_user=False):
        """Handle the /ayuda command, joining every help section in one message."""
        md = bot.get_markdown_helpers()
        sections = self._get_help_sections(bot, partner, internal_user, chat=chat)

        # Build consolidated message
        message_parts = []
        for title, commands in sections:
            if commands:  # Only add section if it has commands
                message_parts.append(md["bold"](f"📋 {title}:"))
                message_parts.extend(commands)
                message_parts.append("")  # Empty line between sections

        # Remove trailing empty line
        if message_parts and not message_parts[-1]:
            message_parts.pop()

        final_message = md["newline"].join(message_parts)
        bot.send_message(chat.chat_id, final_message)

    @log_command_execution
    def _handle_whoami_command(
        self, bot, chat, args, partner=False, internal_user=False
    ):
        """Report whether the chat is linked to a partner or internal user."""
        md = bot.get_markdown_helpers()

        if internal_user:
            user_message = (
                "Estás operando como el usuario de Odoo:\n"
                f"- {md['bold']('Nombre:')} {internal_user.name}\n"
                f"- {md['bold']('Correo:')} {internal_user.login}"
            )
        elif partner:
            user_message = (
                "Este chat de Telegram está vinculado al contacto de Odoo:\n"
                f"- {md['bold']('Nombre:')} {partner.name}"
            )
        else:  # Should not happen due to permission check, but just in case.
            user_message = "Este chat no está vinculado a ningún contacto de Odoo."
        bot.send_message(chat.chat_id, user_message)

    def _handle_shared_contact(self, bot, chat, message, partner, internal_user):
        """Handle a shared contact: linking by phone is not supported, so this
        only acknowledges it.

        :param bot: telegram.bot record
        :param chat: telegram.chat record
        :param dict message: message payload containing the contact
        :param partner: current res.partner record or False
        :param internal_user: res.users record or False
        """
        if partner:
            md = bot.get_markdown_helpers()
            bot.send_message(
                chat.chat_id,
                f"Tu chat ya está vinculado a: {md['bold'](partner.name)}",
                reply_markup={"remove_keyboard": True},
            )
            return

        # Linking by phone is not supported: an administrator links the chat
        # by the user's Telegram ID.
        _logger.info(
            "Received shared contact for chat %s - manual linking required",
            chat.chat_id,
        )
        bot.send_message(
            chat.chat_id,
            (
                "Para vincular tu chat con tu cuenta de Odoo, escribe /start y pásale "
                "tu ID de Telegram al administrador."
            ),
            reply_markup={"remove_keyboard": True},
        )
