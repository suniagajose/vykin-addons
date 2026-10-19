{
    "name": "Telegram Bot Project Task",
    "summary": "Create project tasks from Telegram with voice, text and photos",
    "description": """
Telegram Bot Project Task
=========================
A linked internal user creates a project task by talking to the bot.

* ``/nueva``, or any message sent while the chat is free, starts the flow. The
  command must be enabled on the bot, also for the implicit start.
* The user sends text, voice notes (transcribed with the bot's AI service; the
  transcript is shown back) and up to 5 photos, then taps *Continue*.
* Project: the bot's default project, or one picked among the projects the user
  can read.
* The AI proposes a title and a description; without AI, or when its answer is
  not valid, the draft is built from the user's own text.
* The user creates, edits the title or the description, changes the project or
  discards. A button left over from an earlier draft, pressed while the new flow
  has nothing yet, asks for the story instead of drafting an empty task. The
  task is created with the user's access rights, assigned to them, with the
  photos attached, and the bot answers with a link to it.
* Per bot: default project and a glossary of terms that guides the
  transcription and the drafting.
    """,
    "category": "Project",
    "author": "Vykin",
    "website": "https://vykin.tech/",
    "version": "20.0.1.0.0",
    "license": "LGPL-3",
    "depends": [
        "telegram_bot",
        "project",
    ],
    "data": [
        "data/telegram_command_data.xml",
        "views/telegram_bot_views.xml",
    ],
}
