from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestIrConfigParameter(TransactionCase):
    def test_license_action_resets_parameters(self):
        action = self.env.ref("vykin.license_action_reset")
        params = self.env["ir.config_parameter"].sudo()
        previous_secret = params.get_str("database.secret")

        action.run()

        self.assertNotEqual(params.get_str("database.secret"), previous_secret)
        for key in (
            "database.secret",
            "database.uuid",
            "database.create_date",
            "database.expiration_date",
        ):
            self.assertTrue(params.get_str(key), f"{key} should have a value")
