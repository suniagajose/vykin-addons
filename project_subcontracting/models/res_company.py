from odoo import Command, api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = 'res.company'

    provision_commission_percent = fields.Float(
        string="Commission %", default=20.0,
        help="Share of what each customer payment brings in that goes to the salesperson of the invoice.",
    )
    provision_implementation_percent = fields.Float(
        string="Implementation %", default=75.0,
        help="Share of what each customer payment brings in that goes to the implementers. "
             "The company keeps the rest as income.",
    )
    provision_commission_expense_account_id = fields.Many2one(
        'account.account', string="Commission Expense",
        help="Account charged with the commission when it is provisioned.",
    )
    provision_implementation_expense_account_id = fields.Many2one(
        'account.account', string="Implementation Cost",
        help="Account charged with the implementation cost when it is provisioned.",
    )
    provision_journal_id = fields.Many2one(
        'account.journal', string="Provision Journal", domain="[('type', '=', 'general')]",
        help="Journal of the provision entries.",
    )

    @api.constrains('provision_commission_percent', 'provision_implementation_percent')
    def _check_provision_percents(self):
        for company in self:
            commission, implementation = company.provision_commission_percent, company.provision_implementation_percent
            if commission < 0 or implementation < 0 or commission + implementation > 100:
                raise ValidationError(self.env._(
                    "The commission and implementation percentages of %s cannot be negative and cannot add up to more than 100.",
                    company.display_name,
                ))

    @api.constrains(
        'provision_journal_id', 'provision_commission_expense_account_id', 'provision_implementation_expense_account_id',
    )
    def _check_provision_company(self):
        """ The journal and the expense accounts of the provision belong to the company or to one of its
        parents, as Odoo lets branches use the accounts of their parent company, and the journal is secured
        with a hash, which is what keeps the provision entries from being changed (R7.4). """
        for company in self:
            journal = company.provision_journal_id
            if journal and not journal.restrict_mode_hash_table:
                raise ValidationError(self.env._(
                    "The provision journal %s must be secured with a hash, so that its entries cannot be changed.",
                    journal.display_name,
                ))
            accounts = company.provision_commission_expense_account_id | company.provision_implementation_expense_account_id
            if (journal and journal.company_id not in company.parent_ids) or accounts.filtered(
                lambda account: not account.company_ids & company.parent_ids,
            ):
                raise ValidationError(self.env._(
                    "The provision journal and expense accounts of %s must belong to it or to its parent company.",
                    company.display_name,
                ))

    def _get_provision_account_by_code(self, code, name, account_type, provision_type=False):
        """ Return the account of this company with this code, created if missing, with its provision
        type. An existing account keeps its name. """
        self.ensure_one()
        Account = self.env['account.account'].with_company(self)
        account = Account.search([*Account._check_company_domain(self), ('code', '=', code)], limit=1) or Account.create({
            'code': code,
            'name': name,
            'account_type': account_type,
            'company_ids': [Command.set(self.ids)],
        })
        if provision_type:
            account.provision_type = provision_type
        return account

    def _get_provision_journal_code(self):
        """ Return RPT, or RPT1, RPT2... when the company already has a journal with it. """
        self.ensure_one()
        used = set(self.env['account.journal'].with_context(active_test=False).search(
            [('company_id', '=', self.id), ('code', '=like', 'RPT%')],
        ).mapped('code'))
        return next(code for code in ('RPT', *(f'RPT{i}' for i in range(1, 100))) if code not in used)

    def _setup_provisions(self):
        """ Create the provision accounts, the expense accounts and the journal of the companies,
        reusing the accounts that already exist with the same code, and give the commission and
        subcontracted hours products their provision as expense account (R1.4). No product or
        category gets a provision account: it is a decision of each sale. """
        commission_product = self.env.ref('project_subcontracting.product_commission')
        hours_product = self.env.ref('project_subcontracting.product_subcontracted_hours')
        for company in self:
            get_account = company._get_provision_account_by_code
            commission_provision = get_account('205.02.02', "Provisión de comisiones", 'liability_current', 'commission')
            implementation_provision = get_account(
                '205.02.03', "Provisión de implementación", 'liability_current', 'implementation',
            )
            company.provision_commission_expense_account_id = (
                company.provision_commission_expense_account_id
                or get_account('601.74.01', "Comisiones sobre ventas", 'expense')
            )
            company.provision_implementation_expense_account_id = (
                company.provision_implementation_expense_account_id
                or get_account('501.02.02', "Costo de implementación", 'expense_direct_cost')
            )
            company.provision_journal_id = company.provision_journal_id or self.env['account.journal'].create({
                'name': "Repartos",
                'code': company._get_provision_journal_code(),
                'type': 'general',
                'company_id': company.id,
                'restrict_mode_hash_table': True,
            })
            for product, provision in (
                (commission_product, commission_provision), (hours_product, implementation_provision),
            ):
                product = product.with_company(company)
                product.property_account_expense_id = product.property_account_expense_id or provision
