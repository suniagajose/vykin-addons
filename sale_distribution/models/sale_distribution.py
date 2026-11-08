from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class SaleDistribution(models.Model):
    _name = 'sale.distribution'
    _description = "Distribution Scheme"
    _order = 'company_id, is_default desc, name'
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', required=True, index=True, default=lambda self: self.env.company,
    )
    is_default = fields.Boolean(
        string="Default",
        help="Scheme given to the new sales orders of the company.",
    )
    rule_ids = fields.One2many('sale.distribution.rule', 'distribution_id', string="Rules", copy=True)
    total_percentage = fields.Float(string="Total (%)", compute='_compute_total_percentage')
    is_used = fields.Boolean(
        compute='_compute_is_used',
        help="A sales order with this scheme already has provision entries.",
    )

    @api.depends('rule_ids.percentage')
    def _compute_total_percentage(self):
        for distribution in self:
            distribution.total_percentage = sum(distribution.rule_ids.mapped('percentage'))

    def _compute_is_used(self):
        used = self._get_used()
        for distribution in self:
            distribution.is_used = distribution in used

    @api.constrains('rule_ids')
    def _check_total_percentage(self):
        for distribution in self:
            if float_compare(distribution.total_percentage, 100.0, precision_digits=2):
                raise ValidationError(self.env._(
                    "The rules of the distribution scheme %s must add up to 100%%.", distribution.display_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        distributions = super(SaleDistribution, self.with_context(sale_distribution_skip_total=True)).create(vals_list)
        distributions._check_total_percentage()
        distributions.filtered('is_default')._unset_other_defaults()
        return distributions.with_env(self.env)

    def write(self, vals):
        res = super(SaleDistribution, self.with_context(sale_distribution_skip_total=True)).write(vals)
        if vals.get('is_default'):
            self._unset_other_defaults()
        return res

    def _unset_other_defaults(self):
        self.search([
            ('company_id', 'in', self.company_id.ids),
            ('is_default', '=', True),
            ('id', 'not in', self.ids),
        ]).is_default = False

    def _get_used(self):
        """ Return the schemes whose rules already have provision entries. """
        if not self:
            return self
        # sudo: sales managers without accounting access must know whether a scheme is used.
        groups = self.env['account.move.line'].sudo()._read_group(
            [('distribution_rule_id.distribution_id', 'in', self.ids)],
            ['distribution_rule_id'],
        )
        return self.browse({rule.distribution_id.id for rule, in groups}) & self

    def _get_rule(self, role):
        self.ensure_one()
        return self.rule_ids.filtered(lambda rule: rule.role == role)[:1]
