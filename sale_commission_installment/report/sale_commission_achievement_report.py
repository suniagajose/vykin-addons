from odoo import api, models
from odoo.tools import SQL


class SaleCommissionAchievementReport(models.Model):
    _inherit = 'sale.commission.achievement.report'

    @api.model
    def _where_invoices(self):
        # The parent joins every product line with every payment term line of the
        # invoice, so the achievement is summed once per installment. Keep only the
        # payment term line with the latest full reconciliation: the sum is counted
        # once and MAX(afr.write_date) stays the same as in standard.
        # Remove when sale_commission stops joining payment term lines in
        # _join_invoices.
        return SQL("""%s
          AND pay_line.id IN (
              SELECT DISTINCT ON (term_line.move_id) term_line.id
                FROM account_move_line term_line
                JOIN filtered_moves term_move ON term_move.id = term_line.move_id
           LEFT JOIN account_full_reconcile term_reconcile
                  ON term_reconcile.id = term_line.full_reconcile_id
               WHERE term_line.display_type = 'payment_term'
            ORDER BY term_line.move_id, term_reconcile.write_date DESC NULLS LAST, term_line.id
          )
        """, super()._where_invoices())
