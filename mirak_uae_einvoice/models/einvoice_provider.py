# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..connectors import CONNECTORS


class MirakAeProvider(models.Model):
    """An Accredited Service Provider (ASP) account: where the company's PINT-AE documents are sent."""
    _name = 'mirak.ae.provider'
    _description = 'UAE E-Invoicing Service Provider (ASP)'
    _order = 'sequence, id'

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', default=lambda self: self.env.company, index=True,
                                 help='Empty: shared by all companies.')
    connector = fields.Selection(selection='_selection_connector', required=True, default='mock')
    environment = fields.Selection([('sandbox', 'Sandbox (tests)'), ('production', 'Production')],
                                   default='sandbox', required=True)
    api_url = fields.Char(string='API URL', help='Base address of the provider API, e.g. https://api.provider.ae/v1')
    api_key = fields.Char(string='API Key', groups='mirak_uae_einvoice.group_mirak_ae_manager', copy=False,
                          help='Kept in the database only; never written in the logs or the chatter.')
    timeout = fields.Integer(default=30)
    # generic REST settings
    auth_header = fields.Char(default='Authorization')
    auth_prefix = fields.Char(default='Bearer ', help='Put before the key in the header (e.g. "Bearer ").')
    test_path = fields.Char(default='/status')
    send_path = fields.Char(default='/documents',
                            help='Placeholders: {number}, {uuid}, {type}, {sender}, {receiver}.')
    status_path = fields.Char(default='/documents/{reference}')
    lookup_path = fields.Char(default='/participants/{scheme}:{identifier}',
                              help='Empty when the provider has no participant lookup.')
    json_id_key = fields.Char(string='JSON: document id', default='id')
    json_status_key = fields.Char(string='JSON: status', default='status')
    json_message_key = fields.Char(string='JSON: message', default='message')
    json_registered_key = fields.Char(string='JSON: registered flag', default='registered')
    # test connector
    mock_behaviour = fields.Selection([('accept', 'Accept every document'), ('reject', 'Reject every document'),
                                       ('alternate', 'Accept or reject (alternately)'),
                                       ('error', 'Fail (provider error)')], default='accept')
    last_test_date = fields.Datetime(readonly=True)
    last_test_ok = fields.Boolean(readonly=True)
    last_test_message = fields.Char(readonly=True)
    supports_lookup = fields.Boolean(compute='_compute_supports_lookup')

    @api.model
    def _selection_connector(self):
        # trial build: the REST connector (a real Accredited Service Provider) is reserved to the full version
        return [(code, cls.name) for code, cls in CONNECTORS.items() if code != 'rest']

    @api.depends('connector', 'lookup_path')
    def _compute_supports_lookup(self):
        for provider in self:
            cls = CONNECTORS.get(provider.connector)
            provider.supports_lookup = bool(cls and cls.supports_lookup and
                                            (provider.connector != 'rest' or provider.lookup_path))

    def _get_connector(self):
        self.ensure_one()
        cls = CONNECTORS.get(self.connector)
        if not cls:
            raise UserError(_('Unknown connector "%s".', self.connector))
        return cls(self)

    def action_test_connection(self):
        self.ensure_one()
        result = self._get_connector().test_connection()
        self.sudo().write({'last_test_date': fields.Datetime.now(), 'last_test_ok': result.ok,
                           'last_test_message': (result.message or '')[:250]})
        return {
            'type': 'ir.actions.client', 'tag': 'display_notification',
            'params': {
                'title': _('Connection works') if result.ok else _('Connection failed'),
                'message': result.message, 'type': 'success' if result.ok else 'danger', 'sticky': not result.ok,
            },
        }
