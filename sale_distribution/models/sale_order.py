from contextlib import contextmanager

from odoo import Command, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare


def split_amount(currency, amount, weights):
    """ Split ``amount`` in proportion to ``weights`` ({key: weight}); the last key takes the
    rounding difference, so that the parts always add up to ``amount``. """
    total_weight = sum(weights.values())
    parts = {}
    if not total_weight:
        return parts
    remaining = amount
    keys = list(weights)
    for key in keys[:-1]:
        parts[key] = currency.round(amount * weights[key] / total_weight)
        remaining -= parts[key]
    parts[keys[-1]] = currency.round(remaining)
    return parts


def commercial_partner(beneficiary):
    """ Accounting partner of a salesperson (user) or of a partner: its commercial entity. """
    partner = beneficiary.partner_id if beneficiary._name == 'res.users' else beneficiary
    return partner.commercial_partner_id


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    distribution_id = fields.Many2one(
        'sale.distribution',
        string="Distribution Scheme",
        compute='_compute_distribution_id', store=True, readonly=False, precompute=True,
        check_company=True,
        help="How the payments of this order are provisioned. Without a scheme, nothing is provisioned.",
    )
    distribution_salesperson_ids = fields.One2many(
        'sale.distribution.salesperson', 'order_id',
        string="Distribution Salespeople",
        compute='_compute_distribution_salesperson_ids', store=True, readonly=False, precompute=True,
        copy=True,
    )
    distribution_registered = fields.Boolean(
        compute='_compute_distribution_registered',
        help="The order already has provision entries.",
    )
    distribution_accrual_ids = fields.One2many(
        'sale.distribution.accrual', 'order_id', string="Distribution Provisions", readonly=True,
    )
    distribution_accrual_count = fields.Integer(compute='_compute_distribution_accrual_count')
    distribution_currency_id = fields.Many2one(related='company_id.currency_id')
    distribution_commission_amount = fields.Monetary(
        string="Commission", compute='_compute_distribution_accrual_count', currency_field='distribution_currency_id',
        help="Commission provisioned by the payments of this order.",
    )

    @api.depends('company_id')
    def _compute_distribution_id(self):
        defaults = {
            distribution.company_id: distribution
            for distribution in self.env['sale.distribution'].search([
                ('company_id', 'in', self.company_id.ids), ('is_default', '=', True),
            ])
        }
        registered = self._get_distribution_registered()
        for order in self - registered:
            if order.distribution_id.company_id != order.company_id:
                order.distribution_id = defaults.get(order.company_id, False)
        for order in registered:
            order.distribution_id = order.distribution_id

    @api.depends('user_id')
    def _compute_distribution_salesperson_ids(self):
        registered = self._get_distribution_registered()
        for order in self:
            if order in registered:
                order.distribution_salesperson_ids = order.distribution_salesperson_ids
                continue
            commands = [Command.clear()]
            if order.user_id:
                commands.append(Command.create({'user_id': order.user_id.id, 'percentage': 100.0}))
            order.distribution_salesperson_ids = commands

    def _compute_distribution_registered(self):
        registered = self._get_distribution_registered()
        for order in self:
            order.distribution_registered = order in registered

    def _compute_distribution_accrual_count(self):
        totals = {
            order: (count, commission)
            for order, count, commission in self.env['sale.distribution.accrual']._read_group(
                [('order_id', 'in', self.ids)], ['order_id'], ['__count', 'commission_amount:sum'],
            )
        }
        for order in self:
            order.distribution_accrual_count, order.distribution_commission_amount = totals.get(order, (0, 0.0))

    @api.constrains('distribution_salesperson_ids')
    def _check_distribution_salesperson_ids(self):
        for order in self:
            salespeople = order.distribution_salesperson_ids
            if salespeople and float_compare(sum(salespeople.mapped('percentage')), 100.0, precision_digits=2):
                raise ValidationError(self.env._(
                    "The salespeople of %s must add up to 100%% of the salesperson share.",
                    order.display_name,
                ))

    def write(self, vals):
        if 'distribution_id' in vals:
            changed = self.filtered(lambda order: order.distribution_id.id != vals['distribution_id'])
            if changed._get_distribution_registered():
                raise UserError(self.env._(
                    "The distribution scheme of an order cannot be changed once the order has "
                    "provision entries.",
                ))
        if 'distribution_salesperson_ids' not in vals:
            return super().write(vals)
        with self._distribution_track_salespeople():
            return super(SaleOrder, self.with_context(sale_distribution_tracking=True)).write(vals)

    def action_confirm(self):
        missing = self.filtered(lambda order: order.distribution_id and not order.distribution_salesperson_ids)
        if missing:
            raise UserError(self.env._(
                "Add at least one salesperson to the distribution of %s before confirming it.",
                missing.mapped('display_name'),
            ))
        return super().action_confirm()

    def action_view_distribution_analytic(self):
        """ Analytic items of the order's analytic account, by financial account (R8.1). """
        self.ensure_one()
        analytic = self.project_account_id
        action = self.env['ir.actions.act_window']._for_xml_id('analytic.account_analytic_line_action_entries')
        action['domain'] = [(analytic.plan_id._column_name(), '=', analytic.id)] if analytic else [('id', '=', False)]
        action['context'] = {'group_by': ['general_account_id'], 'search_default_group_by_account': False}
        action['display_name'] = self.env._("Analytic Report of %s", self.name)
        return action

    def action_view_distribution_accruals(self):
        self.ensure_one()
        action = self.env['ir.actions.act_window']._for_xml_id('sale_distribution.sale_distribution_accrual_action')
        action['domain'] = [('order_id', '=', self.id)]
        return action

    # -------------------------------------------------------------------------
    # DISTRIBUTION
    # -------------------------------------------------------------------------

    def _get_distribution_registered(self):
        """ Return the orders with provision entries, even reversed ones. """
        if not self:
            return self
        # sudo: salespeople without accounting access must know whether the order has entries.
        groups = self.env['account.move.line'].sudo()._read_group(
            [('distribution_order_id', 'in', self.ids)], ['distribution_order_id'],
        )
        return self.browse({order.id for order, in groups})

    def _get_distribution_analytic_distribution(self):
        self.ensure_one()
        analytic = self.project_account_id
        return {str(analytic.id): 100.0} if analytic else False

    def _get_distribution_shares(self, base, salesperson_weights):
        """ Return the provision of ``base`` as [(rule, beneficiary, amount)], for the rules
        with accounts. The salesperson share is split by ``salesperson_weights``
        ({user or partner: weight}); the other shares have no beneficiary. """
        self.ensure_one()
        currency = self.company_id.currency_id
        shares = []
        for rule in self.distribution_id.rule_ids.filtered('payable_account_id'):
            amount = currency.round(base * rule.percentage / 100.0)
            if currency.is_zero(amount):
                continue
            if rule.role != 'salesperson':
                shares.append((rule, None, amount))
                continue
            for beneficiary, part in split_amount(currency, amount, salesperson_weights).items():
                if not currency.is_zero(part):
                    shares.append((rule, beneficiary, part))
        return shares

    def _prepare_distribution_line_vals(self, rule, beneficiary, amount, name):
        """ Expense debit and provision credit of ``amount`` (negative to reverse), with the
        order's analytic account; on behalf of the beneficiary's commercial entity. """
        self.ensure_one()
        common = {
            'name': name,
            'partner_id': beneficiary and commercial_partner(beneficiary).id,
            'analytic_distribution': self._get_distribution_analytic_distribution(),
            'distribution_order_id': self.id,
            'distribution_rule_id': rule.id,
        }
        return [
            {**common, 'account_id': rule.expense_account_id.id, 'balance': amount},
            {**common, 'account_id': rule.payable_account_id.id, 'balance': -amount},
        ]

    def _prepare_distribution_move_vals(self, lines_vals, date, ref):
        self.ensure_one()
        company = self.company_id
        company._distribution_check_config()
        return {
            'move_type': 'entry',
            'journal_id': company.distribution_journal_id.id,
            'company_id': company.id,
            'date': date,
            'ref': ref,
            'line_ids': [Command.create(vals) for vals in lines_vals],
        }

    def _get_distribution_provisioned_by_salesperson(self):
        """ Return {commercial entity: amount} still provisioned for each salesperson of this
        order, the weights of a credit note reversal (R7.2). """
        self.ensure_one()
        rule = self.distribution_id._get_rule('salesperson')
        # sudo: the provision of the order is read for whoever posts the credit note.
        groups = self.env['account.move.line'].sudo()._read_group(
            [
                ('distribution_order_id', '=', self.id),
                ('distribution_rule_id', '=', rule.id),
                ('account_id', '=', rule.payable_account_id.id),
                ('parent_state', '=', 'posted'),
            ],
            ['partner_id'],
            ['balance:sum'],
        )
        return {
            partner.with_env(self.env): -balance
            for partner, balance in groups
            if partner and self.company_id.currency_id.compare_amounts(-balance, 0.0) > 0
        }

    def _get_distribution_owed(self, exclude_move=None):
        """ What is still owed of the distributed lines of this order (R2.2, R7.2): their
        subtotal, less what credit notes credited and the bases already collected. """
        self.ensure_one()
        lines = self.env['account.move.line'].search([
            ('sale_line_ids.order_id', '=', self.id),
            ('is_distributed', '=', True),
            ('parent_state', '=', 'posted'),
            ('move_id.move_type', 'in', ('out_invoice', 'out_refund')),
            ('move_id', '!=', exclude_move and exclude_move.id),
        ])
        # sudo: the bases are system data, read for whoever posts the credit note. A base
        # whose provision is reversed (its payment is being unreconciled) is not collected.
        collected = sum(
            self.sudo().distribution_accrual_ids.filtered(
                lambda accrual: not accrual.provision_move_id.reversal_move_ids,
            ).mapped('collected_untaxed_amount'),
        )
        return -sum(lines.mapped('balance')) - collected

    def _distribution_salespeople_summary(self):
        self.ensure_one()
        return ", ".join(
            f"{salesperson.user_id.name} {salesperson.percentage:g}%"
            for salesperson in self.distribution_salesperson_ids
        )

    @contextmanager
    def _distribution_track_salespeople(self):
        """ Changes to the salespeople of orders with provision entries: only sales
        administrators make them, they apply to later payments only, and they are logged in
        the order's chatter (R3.1.3, R3.1.4). """
        registered = self._get_distribution_registered()
        # Checked whatever the context, which an RPC caller chooses.
        if registered and not self.env.su and not self.env.user.has_group('sales_team.group_sale_manager'):
            raise AccessError(self.env._(
                "Only sales administrators can change the salespeople of an order that already has "
                "provision entries.",
            ))
        if self.env.context.get('sale_distribution_tracking'):
            # The order's write already logs the change.
            yield
            return
        before = {order: order._distribution_salespeople_summary() for order in registered}
        yield
        for order, summary in before.items():
            after = order._distribution_salespeople_summary()
            if after != summary:
                order.message_post(body=self.env._(
                    "Distribution salespeople changed from %(before)s to %(after)s. The change applies to "
                    "the payments reconciled from now on.",
                    before=summary, after=after,
                ))
