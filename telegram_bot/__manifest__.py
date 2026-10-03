{
    "name": "Telegram Bot",
    "summary": "Telegram bot core: webhook, chats, messages, user linking, commands",
    "description": """
Telegram Bot
============
Core infrastructure to talk to Odoo users through a Telegram bot. Command and
flow modules (e.g. task creation) build on top of it.

* Bot configuration: the bot token and the webhook secret are fields visible
  only to administrators. The *Register webhook* button calls Telegram's
  ``setWebhook`` with this database's URL and the secret (generated if empty).
* Webhook ``/telegram/webhook/<token>``: an update is ignored unless the token
  matches a bot and the ``X-Telegram-Bot-Api-Secret-Token`` header matches its
  secret. A bot without a secret ignores every update.
* Chats and messages are logged, and each chat keeps a conversation state.
* Linking: a private chat is linked to the contact whose *Telegram user ID*
  (set by an administrator) equals the sender's numeric Telegram ID. The
  @username is never used to link: anyone can take a freed one. ``/start``
  shows an unlinked user their ID so they can ask for the link.
* Three permission levels: unlinked, linked contact, internal user. Commands of
  an internal user run with that user's access rights.
* Commands: ``/start``, ``/ayuda``, ``/quiensoy``.
    """,
    "category": "Productivity",
    "author": "Vykin",
    "website": "https://vykin.tech/",
    "version": "20.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "base",
        "web",
    ],
    "data": [
        "security/ir.access.csv",
        "data/ir_cron.xml",
        "views/telegram_bot_views.xml",
        "views/telegram_chat_views.xml",
        "views/telegram_message_views.xml",
        "views/telegram_command_views.xml",
        "views/res_partner_views.xml",
        "views/telegram_bot_menu.xml",
    ],
}
