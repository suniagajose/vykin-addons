{
    'name': 'Base Vykin',
    'summary': 'Base installation of Vykin: apps, Mexican localization and company data',
    'description': """
Base Vykin
==========
Installs the set of apps used by Vykin and configures its company:

* CRM, Sales, Project, Surveys (with lead generation), Contacts and Calendar.
* Mexican accounting localization (``l10n_mx``).
* Company name, address, website and logo.

The database must be created with Mexico as country (MXN currency) *before*
installing this module, so that the ``account`` module loads the Mexican
chart of accounts on install.
    """,
    'category': 'Vykin',
    'author': 'Vykin',
    'website': 'https://vykin.tech/',
    'version': '20.0.1.0.0',
    'license': 'LGPL-3',
    'application': True,
    'depends': [
        'contacts',
        'calendar',
        'crm',
        'sale_management',
        'sale_crm',
        'project',
        'project_todo',
        'survey',
        'survey_crm',
        'l10n_mx',
    ],
    'data': [
        'data/res_company_data.xml',
    ],
}
