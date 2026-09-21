from odoo import SUPERUSER_ID, api

# Tool name (ai_tool_name) -> XML id declared in data/ir_actions_server_mcp_tools.xml
TOOLS = {
    'create_project_task': 'ir_actions_server_create_project_task',
    'update_project_task': 'ir_actions_server_update_project_task',
    'create_knowledge_article': 'ir_actions_server_create_knowledge_article',
    'update_knowledge_article': 'ir_actions_server_update_knowledge_article',
}


def migrate(cr, version):
    """Adopt the MCP tools that already exist in the database.

    The four tools were first created by hand (no XML id). ``ai_tool_name`` is
    unique among the actions used in AI, so loading the data file on such a
    database would fail instead of updating them. Giving the existing actions
    their XML id first makes the data file update them in place.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    data_list = []
    for tool_name, xml_name in TOOLS.items():
        xml_id = f'vykin.{xml_name}'
        if env.ref(xml_id, raise_if_not_found=False):
            continue
        action = env['ir.actions.server'].search([('ai_tool_name', '=', tool_name)], limit=1)
        if action:
            data_list.append({'xml_id': xml_id, 'record': action, 'noupdate': False})
    env['ir.model.data']._update_xmlids(data_list)
