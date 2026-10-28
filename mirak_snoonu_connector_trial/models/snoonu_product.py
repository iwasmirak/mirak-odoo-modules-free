# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SnoonuProduct(models.Model):
    _name = 'snoonu.product'
    _description = 'Product on Snoonu'
    _rec_name = 'sku'
    _order = 'state, sku'  # errors first, then products waiting to be sent

    account_id = fields.Many2one('snoonu.account', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='account_id.company_id', store=True)
    active = fields.Boolean(default=True)
    product_id = fields.Many2one('product.product', required=True, ondelete='cascade', index=True,
                                 domain="[('type', '=', 'consu')]")
    product_tmpl_id = fields.Many2one(related='product_id.product_tmpl_id', store=True)
    sku = fields.Char(string='SKU', required=True,
                      compute='_compute_sku', store=True, readonly=False, precompute=True,
                      help='Must be the SKU known by Snoonu. Defaults to the Internal Reference.')
    is_available = fields.Boolean(
        string='Visible on Snoonu', default=True,
        help='Off: the product is hidden in the Snoonu app (IsAvailable = false); its stock is still sent.')
    state = fields.Selection([('pending', 'To Send'), ('synced', 'Synced'), ('error', 'Error')],
                             default='pending', required=True, copy=False, index=True)
    last_sync_at = fields.Datetime(string='Last Sent', readonly=True, copy=False)
    last_hash = fields.Char(readonly=True, copy=False)
    last_error = fields.Text(readonly=True, copy=False)
    last_document = fields.Text(string='Last Data Sent', readonly=True, copy=False)
    lst_price = fields.Float(related='product_id.lst_price', string='Sales Price')
    qty_available = fields.Float(related='product_id.qty_available', string='On Hand')

    _account_sku_uniq = models.Constraint('unique(account_id, sku)',
                                          'This SKU is already used on this Snoonu account.')
    _account_product_uniq = models.Constraint('unique(account_id, product_id)',
                                              'This product is already on this Snoonu account.')

    @api.depends('product_id')
    def _compute_sku(self):
        for mapping in self:
            if mapping.product_id and not mapping.sku:
                mapping.sku = mapping.product_id.default_code or mapping.product_id.barcode

    def write(self, vals):
        if {'sku', 'is_available', 'product_id'} & set(vals) and 'state' not in vals:
            vals['state'] = 'pending'
        return super().write(vals)

    def action_sync(self):
        if not self:
            return True
        summary = {'sent': 0, 'ok': 0, 'failed': 0, 'skipped': 0}
        for account, mappings in self.grouped('account_id').items():
            result = account._sync(mappings, force=True)
            for key in summary:
                summary[key] += result[key]
        return self.account_id[:1]._notify_summary(summary)

    def action_hide(self):
        self.write({'is_available': False})
        return self.action_sync()

    def action_show(self):
        self.write({'is_available': True})
        return self.action_sync()

    def action_reset(self):
        self.write({'state': 'pending', 'last_hash': False})

    def action_open_product(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'product.product', 'res_id': self.product_id.id,
                'view_mode': 'form'}

    @api.ondelete(at_uninstall=False)
    def _unlink_warn(self):
        synced = self.filtered(lambda m: m.state == 'synced' and m.is_available)
        if synced and not self.env.context.get('snoonu_force_unlink'):
            raise UserError(_(
                'Removing a product here does not remove it from Snoonu. Hide it first '
                '("Hide on Snoonu"), then remove it: %s', ', '.join(synced.mapped('sku')[:10])))
