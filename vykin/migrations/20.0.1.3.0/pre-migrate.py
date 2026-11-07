from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Disable the weekly Publisher cron on databases that already have it."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref("mail.ir_cron_module_update_notification", raise_if_not_found=False)
    if cron:
        cron.active = False
