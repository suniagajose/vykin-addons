{
    'name': 'Sale Distribution',
    'summary': 'Provision each customer payment between salespeople, implementers and the company',
    'description': """
Sale Distribution
=================
Splits what each customer payment of an implementation sale brings in, following a
distribution scheme chosen on the sales order (standard: 20% salespeople, 75%
implementation, 5% company).

* Nothing is recorded when the invoice is posted. Each payment reconciled with the
  invoice records its base and a provision entry with the analytic account of the
  order's project: the salesperson share on behalf of each salesperson, the
  implementation share without partner. The company share stays in income.
* Each salesperson gets a request for quotation of a *Sales Commission* for what was
  provisioned on their behalf; the vendor bill consumes the provision.
* A vendor bill against a provision account cannot exceed the provision balance of its
  analytic account (and of the vendor, for commissions).
* Unreconciled payments and credit notes are corrected with reversal entries;
  provision entries are never edited. A project with a provision balance is not
  archived until the balance is moved to the expense accounts.

The provision accounts, the journal, the commission product and the standard scheme
are created for each company on install.
    """,
    'category': 'Sales/Sales',
    'author': 'Vykin',
    'website': 'https://vykin.tech/',
    'version': '20.0.1.0.0',
    'license': 'LGPL-3',
    'depends': [
        'purchase',
        'sale_project',
    ],
    'data': [
        'security/ir.access.csv',
        'views/sale_distribution_views.xml',
        'views/sale_distribution_accrual_views.xml',
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'views/product_template_views.xml',
        'views/res_config_settings_views.xml',
        'views/sale_distribution_menus.xml',
        'wizard/sale_distribution_settlement_views.xml',
    ],
    'post_init_hook': '_setup_companies',
}
