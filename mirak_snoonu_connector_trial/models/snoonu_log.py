# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from datetime import timedelta

from odoo import api, fields, models

KEEP_DAYS = 30
MAX_TEXT = 500_000


class SnoonuLog(models.Model):
    _name = 'snoonu.log'
    _description = 'Snoonu Sync Log'
    _order = 'id desc'
    _rec_name = 'create_date'

    account_id = fields.Many2one('snoonu.account', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='account_id.company_id', store=True)
    trigger = fields.Selection([('manual', 'Manual'), ('cron', 'Automatic'), ('test', 'Test')],
                               default='manual', required=True)
    state = fields.Selection([('success', 'Success'), ('error', 'Error')], required=True)
    sku_count = fields.Integer(string='Products')
    http_status = fields.Integer(string='HTTP Status')
    duration = fields.Float(string='Seconds', digits=(16, 2))
    request = fields.Text(string='Request Sent')
    response = fields.Text(string='Snoonu Response')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            for key in ('request', 'response'):
                if vals.get(key) and len(vals[key]) > MAX_TEXT:
                    vals[key] = vals[key][:MAX_TEXT] + '\n... (truncated)'
        return super().create(vals_list)

    @api.autovacuum
    def _gc_old_logs(self):
        limit = fields.Datetime.now() - timedelta(days=KEEP_DAYS)
        self.sudo().search([('create_date', '<', limit)]).unlink()
