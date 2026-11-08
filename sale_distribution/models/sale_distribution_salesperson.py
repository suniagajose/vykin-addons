from odoo import api, fields, models


class SaleDistributionSalesperson(models.Model):
    _name = 'sale.distribution.salesperson'
    _description = "Distribution Salesperson"
    _order = 'order_id, id'

    order_id = fields.Many2one('sale.order', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    user_id = fields.Many2one('res.users', string="Salesperson", required=True)
    percentage = fields.Float(string="Share (%)", required=True, default=100.0, digits=(16, 2))

    _percentage_range = models.Constraint(
        'CHECK(percentage > 0 AND percentage <= 100)',
        "The share of a salesperson must be greater than 0% and at most 100%.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        orders = self.env['sale.order'].browse({vals['order_id'] for vals in vals_list if vals.get('order_id')})
        with orders._distribution_track_salespeople():
            return super().create(vals_list)

    def write(self, vals):
        # The order a salesperson moves to is checked too.
        orders = self.order_id | self.order_id.browse(vals.get('order_id'))
        with orders._distribution_track_salespeople():
            return super().write(vals)

    def unlink(self):
        with self.order_id._distribution_track_salespeople():
            return super().unlink()
