from odoo import Command, fields
from odoo.tests import new_test_user

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


class DistributionCommon(AccountTestInvoicingCommon):
    """ Common data of the acceptance cases of the spec: company C1 with the standard scheme E
    (20/75/5), project PR1 with analytic A1, order P1 of V1 (60%) and V2 (40%) with L1
    Implementation 1,000 (distributed) and L2 License 1,000, invoice F1 for 2,000. Amounts
    without taxes. """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.company_data['company']
        cls.currency = cls.company.currency_id
        cls.env.user.group_ids |= (
            cls.env.ref('sales_team.group_sale_manager')
            | cls.env.ref('purchase.group_purchase_manager')
            | cls.env.ref('project.group_project_manager')
            | cls.env.ref('analytic.group_analytic_accounting')
        )
        cls.scheme = cls.env['sale.distribution'].search([
            ('company_id', '=', cls.company.id), ('is_default', '=', True),
        ])
        cls.rule_salesperson = cls.scheme._get_rule('salesperson')
        cls.rule_implementer = cls.scheme._get_rule('implementer')
        cls.rule_company = cls.scheme._get_rule('company')
        cls.commission_provision = cls.rule_salesperson.payable_account_id
        cls.implementation_provision = cls.rule_implementer.payable_account_id
        cls.commission_expense = cls.rule_salesperson.expense_account_id
        cls.implementation_expense = cls.rule_implementer.expense_account_id
        cls.commission_product = cls.company.distribution_commission_product_id

        cls.product_implementation = cls._create_product(
            name="Implementación", type='service', invoice_policy='order', list_price=1000.0,
            taxes_id=False, is_distributed=True,
        )
        cls.product_license = cls._create_product(
            name="Licencia", type='service', invoice_policy='order', list_price=1000.0, taxes_id=False,
        )

        project_plan, _other_plans = cls.env['account.analytic.plan']._get_all_plans()
        cls.analytic_1 = cls.env['account.analytic.account'].create({
            'name': "A1", 'plan_id': project_plan.id, 'company_id': cls.company.id,
        })
        cls.analytic_2 = cls.env['account.analytic.account'].create({
            'name': "A2", 'plan_id': project_plan.id, 'company_id': cls.company.id,
        })
        cls.project_1 = cls.env['project.project'].create({
            'name': "PR1", 'account_id': cls.analytic_1.id, 'allow_billable': True, 'company_id': cls.company.id,
        })
        cls.project_2 = cls.env['project.project'].create({
            'name': "PR2", 'account_id': cls.analytic_2.id, 'allow_billable': True, 'company_id': cls.company.id,
        })

        cls.user_v1 = cls._create_user('salesperson_v1', "V1", 'sales_team.group_sale_salesman')
        cls.user_v2 = cls._create_user('salesperson_v2', "V2", 'sales_team.group_sale_salesman')
        cls.implementer_1 = cls.env['res.partner'].create({'name': "I1"})
        cls.no_partner = cls.env['res.partner']

        cls.bank_journal = cls.company_data['default_journal_bank']
        for method_line in cls.bank_journal.inbound_payment_method_line_ids | cls.bank_journal.outbound_payment_method_line_ids:
            method_line.payment_account_id = cls.env['account.account'].create({
                'code': f'101.98.{method_line.id:02d}', 'name': f"Outstanding {method_line.name}",
                'account_type': 'asset_current', 'reconcile': True,
            })

    @classmethod
    def _create_user(cls, login, name, groups, companies=None):
        companies = companies or cls.company
        return new_test_user(
            cls.env, login=login, name=name, groups=groups,
            company_id=companies[:1].id, company_ids=[Command.set(companies.ids)],
        )

    # -------------------------------------------------------------------------
    # Builders
    # -------------------------------------------------------------------------

    @classmethod
    def _create_order(cls, lines=None, salespeople=None, project=None, user=None, **values):
        """ P1 by default: L1 Implementation 1,000 and L2 License 1,000 in PR1 (A1), with V1
        60% and V2 40%. """
        project = cls.project_1 if project is None else project
        if lines is None:
            lines = [(cls.product_implementation, 1000.0), (cls.product_license, 1000.0)]
        order = cls.env['sale.order'].create({
            'partner_id': cls.partner_a.id,
            'user_id': (cls.user_v1 if user is None else user).id,
            'project_id': project.id,
            'order_line': [
                Command.create({
                    'product_id': product.id,
                    'price_unit': price,
                    'product_uom_qty': 1.0,
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': {str(project.account_id.id): 100.0} if project else False,
                })
                for product, price in lines
            ],
            **values,
        })
        if salespeople is None:
            salespeople = [(cls.user_v1, 60.0), (cls.user_v2, 40.0)]
        if salespeople:
            order.distribution_salesperson_ids = [Command.clear()] + [
                Command.create({'user_id': user.id, 'percentage': percentage}) for user, percentage in salespeople
            ]
        return order

    @classmethod
    def _invoice(cls, order, post=True):
        if order.state == 'draft':
            order.action_confirm()
        invoice = order._create_invoices()
        invoice.invoice_date = fields.Date.today()
        if post:
            invoice.action_post()
        return invoice

    @classmethod
    def _create_refund(cls, invoice, order_lines, prices, post=True):
        """ Credit note of ``invoice`` with one line per (sales order line, price). """
        refund = cls.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': invoice.partner_id.id,
            'invoice_date': fields.Date.today(),
            'reversed_entry_id': invoice.id,
            'invoice_line_ids': [
                Command.create({
                    'product_id': line.product_id.id,
                    'quantity': 1.0,
                    'price_unit': price,
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': line.analytic_distribution,
                    'sale_line_ids': [Command.set(line.ids)],
                })
                for line, price in zip(order_lines, prices)
            ],
        })
        if post:
            refund.action_post()
        return refund

    @classmethod
    def _pay(cls, invoice, amount, date=None):
        """ Customer payment of ``amount`` reconciled with the invoice. """
        return cls.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids,
        ).create({
            'amount': amount,
            'payment_date': date or fields.Date.today(),
            'journal_id': cls.bank_journal.id,
            'payment_difference_handling': 'open',
        })._create_payments()

    @classmethod
    def _receivable(cls, move):
        return move.line_ids.filtered(lambda aml: aml.account_id.account_type == 'asset_receivable')

    @classmethod
    def _unreconcile(cls, payment):
        cls._receivable(payment.move_id).remove_move_reconcile()

    @classmethod
    def _reconcile(cls, payment, invoice):
        (cls._receivable(payment.move_id) + cls._receivable(invoice)).reconcile()

    @classmethod
    def _bill(cls, partner, lines, post=True, move_type='in_invoice'):
        """ Vendor bill to ``partner`` with one line per (account, amount, analytic accounts). """
        bill = cls.env['account.move'].create({
            'move_type': move_type,
            'partner_id': partner.id,
            'invoice_date': fields.Date.today(),
            'invoice_line_ids': [
                Command.create({
                    'name': "Servicio",
                    'account_id': account.id,
                    'quantity': 1.0,
                    'price_unit': amount,
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': analytics and {
                        str(analytic.id): percentage for analytic, percentage in analytics
                    },
                })
                for account, amount, analytics in lines
            ],
        })
        if post:
            bill.action_post()
        return bill

    @classmethod
    def _pay_bill(cls, bill, amount=None):
        vals = {'journal_id': cls.bank_journal.id}
        if amount is not None:
            vals.update(amount=amount, payment_difference_handling='open')
        return cls.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=bill.ids,
        ).create(vals)._create_payments()

    @classmethod
    def _commission_bill(cls, purchase, post=True):
        """ Vendor bill of a commission purchase. """
        purchase.button_confirm()
        bill = cls.env['account.move'].browse(purchase.action_create_invoice()['res_id'])
        bill.invoice_date = fields.Date.today()
        if post:
            bill.action_post()
        return bill

    # -------------------------------------------------------------------------
    # Reading
    # -------------------------------------------------------------------------

    def _provision(self, account, analytic, partner=None):
        """ Credit balance of a provision account in an analytic account (and partner). """
        balances = self.env['account.move.line']._get_distribution_balances(account, {analytic.id})
        return self.currency.round(balances.get((account, analytic.id, partner or self.no_partner), 0.0))

    def _bases(self, order):
        return self.env['sale.distribution.accrual'].search(
            [('order_id', '=', order.id)], order='id',
        ).mapped('collected_untaxed_amount')

    def _provision_moves(self, order):
        return self.env['account.move.line'].search([('distribution_order_id', '=', order.id)]).move_id

    def _commission_purchases(self, order):
        return self.env['purchase.order'].search([
            ('origin', '=', order.name), ('order_line.product_id', '=', self.commission_product.id),
        ], order='id')

    def _purchase_of(self, order, user):
        return self._commission_purchases(order).filtered(lambda po: po.partner_id == user.partner_id)
