from uuid import uuid1, uuid4

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.model
    def _vykin_reset_license(self, days=30):
        """Regenerate the license parameters with an expiration in ``days`` days."""
        now = fields.Datetime.today()
        values = {
            "database.secret": str(uuid4()),
            "database.uuid": str(uuid1()),
            "database.create_date": now.strftime("%Y-%m-%d %H:%M:%S"),
            "database.expiration_date": (now + relativedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        params = self.sudo()
        for key, value in values.items():
            params.set_str(key, value)
        return values
