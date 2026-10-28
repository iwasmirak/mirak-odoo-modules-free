# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, fields, models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    snoonu_count = fields.Integer(compute='_compute_snoonu_count',
                                  groups='mirak_snoonu_connector_trial.group_snoonu_user')

    def _compute_snoonu_count(self):
        counts = dict(self.env['snoonu.product']._read_group(
            [('product_tmpl_id', 'in', self.ids)], ['product_tmpl_id'], ['__count']))
        for template in self:
            template.snoonu_count = counts.get(template, 0)

    def action_open_snoonu(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'snoonu.product', 'name': _('Snoonu'),
                'view_mode': 'list,form', 'domain': [('product_tmpl_id', '=', self.id)]}
