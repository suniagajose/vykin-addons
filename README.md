# vykin-addons

Custom Odoo modules for Vykin. Branch `20.0` targets Odoo 20.0.

## Modules

- `vykin` (Base Vykin): the main app. Installs the Vykin apps (CRM, Sales,
  Project, Surveys, Contacts, Calendar, Knowledge), the Mexican localization
  (`l10n_mx`), sets the company data and holds everything AI-related, one data
  file per feature. Today: MCP tools (server actions exposed through `ai_mcp`)
  to create and update project tasks (with parent task), post internal notes on
  them, and create and update Knowledge articles.
  When a survey participation generates a lead (`survey_crm`), the survey title and
  answers are also logged as an internal note in the lead's chatter.
  The XML is the source of truth: upgrading the module overwrites manual edits of
  those actions. On a database where the tools were created by hand, the
  `20.0.1.1.0` upgrade adopts them (same `ai_tool_name`) instead of duplicating
  them.

Requirements: Odoo Enterprise (`ai_mcp`, `knowledge`) and the PostgreSQL
extension `vector` (pgvector, `postgresql-<version>-pgvector`).

## Install

Create the database with Mexico as country (so the company currency is MXN)
and without demo data, then install the module:

```
odoo-bin db -c vykin200.conf init --country mx --password <admin-password> vykin200
sudo -u postgres psql -d vykin200 -c "CREATE EXTENSION IF NOT EXISTS vector;"
odoo-bin module install -c vykin200.conf -d vykin200 vykin
```

Load `es_MX` only **after** installing the module:

```
odoo-bin -c vykin200.conf -d vykin200 --load-language es_MX --stop-after-init
```

With `es_MX` already active, loading the Mexican chart of accounts fails because
the translated journal code `MISCELÁNEO` does not fit `account_journal.code`
(varchar(7)).
