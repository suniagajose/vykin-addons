from odoo import Command, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = 'res.company'

    distribution_journal_id = fields.Many2one(
        'account.journal',
        string="Distribution Journal",
        check_company=True,
        domain="[('type', '=', 'general')]",
        help="Journal of the provision entries.",
    )
    distribution_commission_product_id = fields.Many2one(
        'product.product',
        string="Commission Product",
        check_company=True,
        domain="[('type', '=', 'service'), ('purchase_ok', '=', True)]",
        help="Product of the purchases that pay each salesperson. Its expense account must be the "
             "provision account of the salesperson rule.",
    )

    def _distribution_check_config(self):
        for company in self:
            if not (company.distribution_journal_id and company.distribution_commission_product_id):
                raise UserError(self.env._(
                    "Set the distribution journal and commission product of %s in the Sales settings.",
                    company.display_name,
                ))

    def _distribution_get_account(self, code, name, account_type):
        """ Return the account of this company with this code, created if missing. """
        self.ensure_one()
        Account = self.env['account.account'].with_company(self)
        return Account.search([*Account._check_company_domain(self), ('code', '=', code)], limit=1) or Account.create({
            'code': code,
            'name': name,
            'account_type': account_type,
            'company_ids': [self.id],
        })

    def _distribution_get_journal_code(self):
        """ Return RPT, or RPT1, RPT2... when the company already has a journal with it. """
        self.ensure_one()
        used = set(self.env['account.journal'].with_context(active_test=False).search(
            [('company_id', '=', self.id), ('code', '=like', 'RPT%')],
        ).mapped('code'))
        return next(code for code in ('RPT', *(f'RPT{i}' for i in range(1, 100))) if code not in used)

    def _distribution_setup(self):
        """ Create the journal, the accounts, the commission product and the standard scheme
        of the companies, reusing the accounts that already exist with the same code. """
        for company in self:
            get_account = company._distribution_get_account
            commission_provision = get_account('205.02.02', "Provisión de comisiones", 'liability_current')
            implementation_provision = get_account('205.02.03', "Provisión de implementación", 'liability_current')
            implementation = get_account('501.02.02', "Costo de implementación", 'expense_direct_cost')
            commission = get_account('601.74.01', "Comisiones sobre ventas", 'expense_direct_cost')
            journal = company.distribution_journal_id or self.env['account.journal'].create({
                'name': "Repartos",
                'code': company._distribution_get_journal_code(),
                'type': 'general',
                'company_id': company.id,
            })
            product = company.distribution_commission_product_id or self.env['product.product'].with_company(company).create({
                'name': "Comisión de venta",
                'type': 'service',
                'sale_ok': False,
                'purchase_ok': True,
                'purchase_method': 'receive',
                'supplier_taxes_id': [Command.clear()],
                'property_account_expense_id': commission_provision.id,
                'company_id': company.id,
            })
            company.write({
                'distribution_journal_id': journal.id,
                'distribution_commission_product_id': product.id,
            })
            if not self.env['sale.distribution'].search_count([('company_id', '=', company.id)], limit=1):
                self.env['sale.distribution'].create({
                    'name': "Estándar",
                    'company_id': company.id,
                    'is_default': True,
                    'rule_ids': [
                        Command.create({
                            'sequence': 10, 'role': 'salesperson', 'percentage': 20.0,
                            'expense_account_id': commission.id,
                            'payable_account_id': commission_provision.id,
                        }),
                        Command.create({
                            'sequence': 20, 'role': 'implementer', 'percentage': 75.0,
                            'expense_account_id': implementation.id,
                            'payable_account_id': implementation_provision.id,
                        }),
                        Command.create({'sequence': 30, 'role': 'company', 'percentage': 5.0}),
                    ],
                })
