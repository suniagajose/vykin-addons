{
    'name': 'Base Vykin',
    'summary': 'Base installation of Vykin: apps, Mexican localization, company data and AI tools',
    'description': """
Base Vykin
==========
Installs the set of apps used by Vykin and configures its company:

* CRM, Sales, Project, Surveys (with lead generation), Contacts and Calendar.
* Mexican accounting localization (``l10n_mx``).
* Company name, address, website and logo.
* AI: MCP tools (server actions exposed through ``ai_mcp``) to create and update
  project tasks (with parent task), post internal notes on them, and create and
  update Knowledge articles. They run with the permissions of the user who owns
  the API key and none of them deletes or archives records. Everything related
  to AI (MCP or not) lives in this module, one data file per feature.

The database must be created with Mexico as country (MXN currency) *before*
installing this module, so that the ``account`` module loads the Mexican
chart of accounts on install.

The AI part needs Odoo Enterprise (``ai_mcp``, ``knowledge``) and the PostgreSQL
extension ``vector`` (pgvector) in the database *before* installing this module:
``ai`` creates it on install, which requires a superuser.
    """,
    'category': 'Vykin',
    'author': 'Vykin',
    'website': 'https://vykin.tech/',
    'version': '20.0.1.2.0',
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
        'knowledge',
        'ai_mcp',
        'survey',
        'survey_crm',
        'l10n_mx',
    ],
    'data': [
        'data/res_company_data.xml',
        'data/ir_actions_server_data.xml',
    ],
}
