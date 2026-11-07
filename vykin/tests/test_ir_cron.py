from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestIrCron(TransactionCase):
    def test_publisher_notification_cron_is_disabled(self):
        cron = self.env.ref("mail.ir_cron_module_update_notification")
        self.assertFalse(cron.active)
