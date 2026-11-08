from . import models, wizard


def _setup_companies(env):
    # Companies whose chart of accounts is loaded later are set up by the chart template.
    companies = env['res.company'].search([('chart_template', '!=', False)])
    companies.filtered(
        lambda company: env['account.account'].search_count([('company_ids', 'in', company.id)], limit=1),
    )._distribution_setup()
