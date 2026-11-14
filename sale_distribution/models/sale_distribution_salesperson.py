from odoo import api, fields, models


class SaleDistributionSalesperson(models.Model):
    _name = 'sale.distribution.salesperson'
    _description = "Distribution Salesperson"
    _order = 'order_id, id'

    order_id = fields.Many2one('sale.order', required=True, index=True, ondelete='cascade')
    company_id = fields.Many2one(related='order_id.company_id', store=True)
    user_id = fields.Many2one('res.users', string="Salesperson", required=True)
    percentage = fields.Float(
        string="Commission Share (%)", required=True, default=100.0, digits=(16, 2),
        help="Share of the scheme's salesperson commission assigned to this salesperson. The shares "
             "of an order must add up to 100%.",
    )
    effective_percentage = fields.Float(
        string="% of Sale", compute='_compute_effective_percentage', digits=(16, 2),
        help="Percentage of each collected amount of the distributed lines that goes to this "
             "salesperson: their commission share times the salesperson percentage of the scheme.",
    )

    _percentage_range = models.Constraint(
        'CHECK(percentage > 0 AND percentage <= 100)',
        "The share of a salesperson must be greater than 0% and at most 100%.",
    )

    @api.depends('percentage', 'order_id.distribution_id.rule_ids.percentage')
    def _compute_effective_percentage(self):
        for salesperson in self:
            rule = salesperson.order_id.distribution_id._get_rule('salesperson')
            salesperson.effective_percentage = salesperson.percentage * rule.percentage / 100.0

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
