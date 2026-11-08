from collections import defaultdict

from odoo import api, fields, models


class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    distribution_accrual_ids = fields.One2many(
        'sale.distribution.accrual', 'partial_reconcile_id', string="Distribution Accruals", readonly=True,
    )

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        for partial in partials:
            partial._distribution_provision()
        return partials

    def unlink(self):
        # Same pattern as the cash basis taxes: what a payment provisioned is reversed when
        # the payment is unreconciled.
        # sudo: the accruals are system data, handled for whoever unreconciles the payment.
        self.sudo().distribution_accrual_ids._cancel_provision()
        return super().unlink()

    def _get_distribution_invoice(self):
        """ Return the customer invoice paid by this reconciliation when it counts as a
        payment to provision: a payment or a bank transaction, not a credit note (R2.3). """
        self.ensure_one()
        for line, counterpart in (
            (self.debit_move_id, self.credit_move_id),
            (self.credit_move_id, self.debit_move_id),
        ):
            invoice = line.move_id
            if (
                invoice.move_type == 'out_invoice'
                and line.account_id.account_type == 'asset_receivable'
                and (counterpart.move_id.origin_payment_id or counterpart.move_id.statement_line_id)
                and any(invoice.invoice_line_ids.mapped('is_distributed'))
                and invoice.invoice_line_ids.sale_line_ids.order_id.filtered('distribution_id')
            ):
                return invoice, counterpart.move_id
        return self.env['account.move'], self.env['account.move']

    def _compute_distribution_bases(self, invoice):
        """ Return {order: base} of this payment of ``invoice`` (R2.2): what is still owed of
        the distributed subtotal of each order (less what earlier credit notes credited and
        earlier payments already collected), times the amount paid over what the invoice
        still owed before this payment, capped by what is still owed of the whole order, so
        that a credit note not reconciled with this invoice is not provisioned again. """
        self.ensure_one()
        currency = invoice.company_currency_id
        receivable = invoice.line_ids.filtered(lambda aml: aml.account_id.account_type == 'asset_receivable')
        earlier = (receivable.matched_debit_ids | receivable.matched_credit_ids).filtered(lambda p: p.id < self.id)
        owed = abs(invoice.amount_total_signed) - sum(earlier.mapped('amount'))
        if currency.compare_amounts(owed, 0.0) <= 0:
            return {}
        pending = defaultdict(float)
        for order, lines in invoice._get_distribution_lines_by_order().items():
            pending[order] -= sum(lines.mapped('balance'))
        for partial in earlier:
            refund = (partial.debit_move_id | partial.credit_move_id).move_id - invoice
            if refund.move_type != 'out_refund':
                continue
            ratio = partial.amount / abs(refund.amount_total_signed)
            for order, lines in refund._get_distribution_lines_by_order().items():
                pending[order] -= sum(lines.mapped('balance')) * ratio
        # sudo: the bases are system data, read here for whoever reconciles the payment.
        for accrual in earlier.sudo().distribution_accrual_ids:
            pending[accrual.order_id] -= accrual.collected_untaxed_amount
        ratio = min(self.amount / owed, 1.0)
        return {
            order: currency.round(min(max(amount, 0.0) * ratio, max(order._get_distribution_owed(), 0.0)))
            for order, amount in pending.items()
        }

    def _distribution_provision(self):
        """ Record the base of this payment by order and provision it (R2.2, R3.2, R4). """
        self.ensure_one()
        invoice, payment_move = self._get_distribution_invoice()
        if not invoice:
            return
        # sudo: a reconciliation provisions on behalf of the system, whoever registers the
        # payment (an accountant may have no access to sales or purchases).
        done = self.sudo().distribution_accrual_ids.order_id
        bases = self._compute_distribution_bases(invoice)
        accruals = self.env['sale.distribution.accrual'].sudo().create([
            {
                'partial_reconcile_id': self.id,
                'invoice_id': invoice.id,
                'payment_move_id': payment_move.id,
                'order_id': order.id,
                'distribution_id': order.distribution_id.id,
                'date': self.max_date,
                'collected_untaxed_amount': base,
            }
            for order, base in bases.items()
            if order not in done and invoice.company_currency_id.compare_amounts(base, 0.0) > 0
        ])
        accruals._create_provision()
