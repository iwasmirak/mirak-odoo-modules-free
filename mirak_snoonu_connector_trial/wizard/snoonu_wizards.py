# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SnoonuAddProducts(models.TransientModel):
    _name = 'snoonu.add.products'
    _description = 'Add Products to Snoonu'

    account_id = fields.Many2one('snoonu.account', required=True,
                                 default=lambda self: self.env['snoonu.account'].search([], limit=1))
    selection = fields.Selection(
        [('selected', 'Selected products'), ('category', 'Product categories'), ('all', 'All saleable goods')],
        default='selected', required=True)
    product_ids = fields.Many2many('product.product', string='Products',
                                   domain="[('type', '=', 'consu'), ('sale_ok', '=', True)]")
    categ_ids = fields.Many2many('product.category', string='Categories')
    sku_source = fields.Selection([('default_code', 'Internal Reference'), ('barcode', 'Barcode')],
                                  string='SKU From', default='default_code', required=True)
    visible = fields.Boolean(string='Visible on Snoonu', default=True)
    send_now = fields.Boolean(string='Send to Snoonu now', default=False)

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        ctx = self.env.context
        if ctx.get('active_model') == 'product.template' and ctx.get('active_ids'):
            variants = self.env['product.template'].browse(ctx['active_ids']).product_variant_ids
            res.update(selection='selected', product_ids=[(6, 0, variants.ids)])
        elif ctx.get('active_model') == 'product.product' and ctx.get('active_ids'):
            res.update(selection='selected', product_ids=[(6, 0, ctx['active_ids'])])
        return res

    def _candidates(self):
        domain = [('type', '=', 'consu'), ('sale_ok', '=', True),
                  ('company_id', 'in', [False, self.account_id.company_id.id])]
        if self.selection == 'selected':
            return self.product_ids
        if self.selection == 'category':
            if not self.categ_ids:
                raise UserError(_('Choose at least one category.'))
            domain.append(('categ_id', 'child_of', self.categ_ids.ids))
        return self.env['product.product'].search(domain)

    def action_add(self):
        self.ensure_one()
        products = self._candidates()
        existing = self.env['snoonu.product'].with_context(active_test=False).search(
            [('account_id', '=', self.account_id.id)])
        used_products, used_skus = set(existing.product_id.ids), set(existing.mapped('sku'))
        vals, no_sku, duplicates = [], [], []
        for product in products:
            if product.id in used_products:
                continue
            sku = (product.default_code if self.sku_source == 'default_code' else product.barcode) or ''
            sku = sku.strip()
            if not sku:
                no_sku.append(product.display_name)
                continue
            if sku in used_skus:
                duplicates.append(sku)
                continue
            used_skus.add(sku)
            vals.append({'account_id': self.account_id.id, 'product_id': product.id, 'sku': sku,
                         'is_available': self.visible})
        created = self.env['snoonu.product'].create(vals)
        summary = None
        if created and self.send_now:
            summary = self.account_id._sync(created, force=True)
        lines = [_('%s products added.', len(created))]
        if no_sku:
            lines.append(_('%(count)s skipped without SKU (e.g. %(names)s).',
                           count=len(no_sku), names=', '.join(no_sku[:3])))
        if duplicates:
            lines.append(_('%(count)s skipped with a duplicate SKU (e.g. %(skus)s).',
                           count=len(duplicates), skus=', '.join(duplicates[:3])))
        if summary:
            lines.append(_('Snoonu: %(ok)s sent, %(failed)s failed.', **summary))
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': _('Snoonu'), 'message': ' '.join(lines),
                           'type': 'warning' if (no_sku or duplicates or (summary and summary['failed'])) else 'success',
                           'sticky': bool(no_sku or duplicates),
                           'next': {'type': 'ir.actions.act_window_close'}}}


class SnoonuPayloadPreview(models.TransientModel):
    _name = 'snoonu.payload.preview'
    _description = 'Snoonu Payload Preview'

    account_id = fields.Many2one('snoonu.account', readonly=True)
    endpoint_url = fields.Char(readonly=True)
    product_count = fields.Integer(readonly=True)
    payload = fields.Text(readonly=True)
