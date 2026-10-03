from odoo import models

# Value of context_data["op"] while a chat is creating a task.
TASK_FLOW_OP = "task_new"


class TelegramChat(models.Model):
    _inherit = "telegram.chat"

    def _get_active_flow(self):
        """Name the task flow, so the busy notice says what is in progress."""
        if self.state == "awaiting_input" and self.get_context("op") == TASK_FLOW_OP:
            return {"label": "una tarea nueva", "cancel_command": None}
        return super()._get_active_flow()
