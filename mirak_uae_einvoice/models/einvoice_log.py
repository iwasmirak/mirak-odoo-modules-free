# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import fields, models

OPERATIONS = [('send', 'Send'), ('status', 'Status check'), ('validate', 'Pre-validation')]
RESULTS = [('sent', 'Sent'), ('accepted', 'Accepted'), ('rejected', 'Rejected'), ('error', 'Error'),
           ('blocked', 'Blocked by pre-validation'), ('ok', 'OK'), ('unknown', 'Unknown')]


class MirakAeLog(models.Model):
    """One exchange with the service provider. Requests are stored redacted: never an API key."""
    _name = 'mirak.ae.log'
    _description = 'UAE E-Invoicing Exchange Log'
    _order = 'id desc'

    move_id = fields.Many2one('account.move', string='Document', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='move_id.company_id', store=True, index=True)
    provider_id = fields.Many2one('mirak.ae.provider', string='Provider', ondelete='set null')
    operation = fields.Selection(OPERATIONS, required=True)
    result = fields.Selection(RESULTS, required=True)
    reference = fields.Char(string='Provider Reference')
    message = fields.Text()
    request = fields.Text(string='Request (redacted)')
    response = fields.Text()
    attempt = fields.Integer()
    create_date = fields.Datetime(string='Date')
