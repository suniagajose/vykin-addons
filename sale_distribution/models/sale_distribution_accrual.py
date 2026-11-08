from odoo import Command, api, fields, models
from odoo.tools import format_amount


class SaleDistributionAccrual(models.Model):
    """ Base of a customer payment, by sales order, with the provision entry it produced.
    Created by the system when the payment is reconciled with the invoice, and frozen: later
    payments of the invoice subtract it from what is still owed (R2.2). Removed with the
    reconciliation, after its provision entry is reversed (R7.1). """
    _name = 'sale.distribution.accrual'
    _description = "Distribution Accrual"
    _order = 'date desc, id desc'

    name = fields.Char(compute='_compute_name', store=True)
    partial_reconcile_id = fields.Many2one(
        'account.partial.reconcile', string="Reconciliation",
        required=True, readonly=True, index=True, ondelete='cascade',
    )
    invoice_id = fields.Many2one('account.move', string="Invoice", required=True, readonly=True, index=True)
    payment_move_id = fields.Many2one('account.move', string="Payment", readonly=True, index='btree_not_null')
    order_id = fields.Many2one('sale.order', string="Sales Order", required=True, readonly=True, index=True)
    distribution_id = fields.Many2one('sale.distribution', string="Scheme", required=True, readonly=True)
    company_id = fields.Many2one(related='invoice_id.company_id', store=True)
    currency_id = fields.Many2one(related='company_id.currency_id')
    date = fields.Date(required=True, readonly=True)
    collected_untaxed_amount = fields.Monetary(string="Base", readonly=True)
    provision_move_id = fields.Many2one('account.move', string="Provision Entry", readonly=True, index='btree_not_null')
    purchase_line_ids = fields.One2many(
        'purchase.order.line', 'distribution_accrual_id', string="Commission Purchase Lines", readonly=True,
    )

    _partial_order_unique = models.Constraint(
        'UNIQUE(partial_reconcile_id, order_id)',
        "A payment is provisioned only once per sales order.",
    )

    @api.depends('invoice_id.name', 'order_id.name', 'payment_move_id.name')
    def _compute_name(self):
        for accrual in self:
            accrual.name = " - ".join(filter(None, (
                accrual.invoice_id.name, accrual.order_id.name, accrual.payment_move_id.name,
            )))

    def _get_payment_amount(self):
        """ Amount of the payment or bank transaction, in its currency, for the commission. """
        self.ensure_one()
        move = self.payment_move_id
        if move.origin_payment_id:
            return format_amount(self.env, move.origin_payment_id.amount, move.origin_payment_id.currency_id)
        line = move.statement_line_id
        return format_amount(self.env, abs(line.amount), line.currency_id)

    def _create_provision(self):
        """ Post the provision entry of each accrual and the commission purchases (R3.2, R4). """
        for accrual in self:
            order = accrual.order_id
            weights = {salesperson.user_id: salesperson.percentage for salesperson in order.distribution_salesperson_ids}
            shares = order._get_distribution_shares(accrual.collected_untaxed_amount, weights)
            if not shares:
                continue
            name = self.env._(
                "Provision of %(payment)s for %(invoice)s",
                payment=accrual.payment_move_id.name, invoice=accrual.invoice_id.name,
            )
            lines_vals = [
                vals
                for rule, beneficiary, amount in shares
                for vals in order._prepare_distribution_line_vals(rule, beneficiary, amount, name)
            ]
            move = self.env['account.move'].create(order._prepare_distribution_move_vals(lines_vals, accrual.date, name))
            move._post(soft=False)
            accrual.provision_move_id = move
            accrual._create_commission_purchases([
                (beneficiary, amount) for rule, beneficiary, amount in shares if rule.role == 'salesperson'
            ])

    def _create_commission_purchases(self, salesperson_amounts):
        """ One request for quotation per salesperson, to their contact, for what was
        provisioned on their behalf by this payment, already received (R3.2). """
        self.ensure_one()
        if not salesperson_amounts:
            return
        company = self.company_id
        product = company.distribution_commission_product_id
        description = self.env._(
            "Commission - %(payment)s - %(amount)s - %(invoice)s",
            payment=self.payment_move_id.name, amount=self._get_payment_amount(), invoice=self.invoice_id.name,
        )
        orders = self.env['purchase.order'].create([
            {
                'partner_id': user.partner_id.id,
                'company_id': company.id,
                'currency_id': company.currency_id.id,
                'origin': self.order_id.name,
                'order_line': [Command.create({
                    'product_id': product.id,
                    'name': description,
                    'product_qty': 1.0,
                    'price_unit': amount,
                    'tax_ids': [Command.clear()],
                    'analytic_distribution': self.order_id._get_distribution_analytic_distribution(),
                    'distribution_accrual_id': self.id,
                })],
            }
            for user, amount in salesperson_amounts
        ])
        orders.order_line.qty_received = 1.0

    def _cancel_provision(self):
        """ Reverse the provision of these payments and cancel their commission purchases,
        unless a commission was already billed; then compute again the excess of the credit
        notes of their orders, since more is owed now (R7.1, R7.1.1, R7.2). """
        if not self:
            return
        purchases = self.purchase_line_ids.order_id
        bills = purchases.invoice_ids.filtered(lambda move: move.state != 'cancel')
        if bills:
            raise bills._get_distribution_blocking_error(
                self.env._("This customer payment cannot be unreconciled: its commissions are already billed."),
                self.env._("Unreconcile the customer payment again: its provision is reversed and its commission purchases are cancelled."),
            )
        moves = self.provision_move_id
        moves._distribution_reverse()
        purchases.button_cancel()
        refund_reversals = moves._distribution_redo_refunds(self.order_id)
        (moves | refund_reversals)._distribution_check_balances()
