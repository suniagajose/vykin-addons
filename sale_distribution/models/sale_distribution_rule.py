from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

PROTECTED_FIELDS = ('distribution_id', 'role', 'percentage', 'expense_account_id', 'payable_account_id')
DISTRIBUTION_ROLES = [
    ('salesperson', "Salesperson"),
    ('implementer', "Implementer"),
    ('company', "Company"),
]


class SaleDistributionRule(models.Model):
    _name = 'sale.distribution.rule'
    _description = "Distribution Rule"
    _order = 'distribution_id, sequence, id'
    _check_company_auto = True

    distribution_id = fields.Many2one(
        'sale.distribution', string="Scheme", required=True, index=True, ondelete='cascade',
    )
    company_id = fields.Many2one(related='distribution_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    role = fields.Selection(selection=DISTRIBUTION_ROLES, required=True)
    percentage = fields.Float(string="Share (%)", required=True, digits=(16, 2))
    expense_account_id = fields.Many2one(
        'account.account',
        string="Expense Account",
        check_company=True,
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost', 'expense_other'))]",
        help="Account debited when a payment is provisioned.",
    )
    payable_account_id = fields.Many2one(
        'account.account',
        string="Provision Account",
        check_company=True,
        domain="[('account_type', '=', 'liability_current')]",
        help="Account credited when a payment is provisioned, and debited by the vendor bills "
             "that pay this share. Without accounts, the share stays in income.",
    )

    _role_unique = models.Constraint(
        'UNIQUE(distribution_id, role)',
        "A distribution scheme can have only one rule per role.",
    )
    _percentage_range = models.Constraint(
        'CHECK(percentage >= 0 AND percentage <= 100)',
        "The share of a rule must be between 0% and 100%.",
    )

    @api.constrains('role', 'expense_account_id', 'payable_account_id')
    def _check_accounts(self):
        for rule in self:
            if rule.role == 'company' and (rule.expense_account_id or rule.payable_account_id):
                raise ValidationError(self.env._("The company share stays in income: its rule takes no accounts."))
            if bool(rule.expense_account_id) != bool(rule.payable_account_id):
                raise ValidationError(self.env._(
                    "The %s rule needs both an expense account and a provision account, or none.",
                    rule._fields['role'].convert_to_export(rule.role, rule),
                ))
            product = rule.company_id.distribution_commission_product_id
            expense = product.with_company(rule.company_id).property_account_expense_id
            if rule.role == 'salesperson' and rule.payable_account_id and expense and expense != rule.payable_account_id:
                raise ValidationError(self.env._(
                    "The provision account of the salesperson rule must be the expense account of the "
                    "commission product %(product)s (%(account)s), so that its vendor bills consume it.",
                    product=product.display_name, account=expense.display_name,
                ))

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules._check_distribution()
        return rules

    def write(self, vals):
        changed = set(vals).intersection(PROTECTED_FIELDS)
        if changed:
            self._check_not_used()
        res = super().write(vals)
        if changed:
            self._check_distribution()
        return res

    def unlink(self):
        self._check_not_used()
        distributions = self.distribution_id
        res = super().unlink()
        if not self.env.context.get('sale_distribution_skip_total'):
            distributions.exists()._check_total_percentage()
        return res

    @api.model
    def _get_payable_accounts(self):
        """ Return (provision accounts, those whose balance is kept by beneficiary): the
        provision accounts of every rule, the second set for the salesperson rules. """
        # sudo: vendor bills and corrections are checked whatever the user can read of schemes.
        rules = self.sudo().with_context(active_test=False).search([('payable_account_id', '!=', False)])
        partner_rules = rules.filtered(lambda rule: rule.role == 'salesperson')
        return rules.payable_account_id, partner_rules.payable_account_id

    def _check_not_used(self):
        if self.distribution_id._get_used():
            raise UserError(self.env._(
                "This distribution scheme already has provision entries: its rules cannot be "
                "changed. Archive it and create a new scheme instead.",
            ))

    def _check_distribution(self):
        # Edited through the scheme, the total is checked once all the rules are written.
        if not self.env.context.get('sale_distribution_skip_total'):
            self.distribution_id._check_total_percentage()
