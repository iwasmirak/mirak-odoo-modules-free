# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
import re

from odoo import api, fields, models

from ..tools import pint_ae

# Taxes of the Odoo UAE localization (l10n_ae), by the end of their external id
L10N_AE_TAXES = [
    ('uae_sale_tax_5_', 'S'),
    ('uae_sale_tax_tourist_refund', 'S'),
    ('uae_sale_tax_exempted', 'E'),
    ('uae_sale_tax_0', 'Z'),
    ('uae_export_tax', 'Z'),
    ('uae_out_of_scope', 'O'),
    ('uae_purchase_tax_reverse_charge', 'AE'),
    ('uae_purchase_tax_exempted', 'E'),
    ('uae_purchase_tax_0', 'Z'),
    ('uae_purchase_tax_5', 'S'),
    ('uae_purchase_postponed_tax', 'S'),
    ('uae_import_tax', 'S'),
]
# Odoo's own UBL category codes (account_edi_ubl_cii, when installed) -> PINT-AE
UBL_CODES = {'S': 'S', 'Z': 'Z', 'E': 'E', 'O': 'O', 'AE': 'AE', 'SR': 'S', 'ZR': 'Z', 'ES': 'E', 'OS': 'O'}
NAME_PATTERNS = [
    (r'reverse|عكسي|\bR\s?C\b', 'AE'),
    (r'exempt|معف|\bEXT\b', 'E'),
    (r'out of scope|outside|خارج', 'O'),
    (r'zero|صفر|export|تصدير', 'Z'),
]


class AccountTax(models.Model):
    _inherit = 'account.tax'

    mirak_ae_category = fields.Selection(pint_ae.TAX_CATEGORIES, string='UAE Tax Category',
                                         compute='_compute_mirak_ae_category', store=True, readonly=False,
                                         help='PINT-AE tax category code put on every invoice line using this tax. '
                                              'Filled automatically; change it if needed.')
    mirak_ae_exemption_code = fields.Selection(pint_ae.EXEMPTION_CODES, string='Exemption Reason',
                                               help='Only for exempt (E) supplies.')

    @api.depends('amount', 'amount_type', 'name')
    def _compute_mirak_ae_category(self):
        external = self._mirak_ae_external_names()
        for tax in self:
            if not tax.mirak_ae_category:
                tax.mirak_ae_category = tax._mirak_ae_guess_category(external.get(tax.id, ''))

    def _mirak_ae_external_names(self):
        ids = [i for i in self.ids if isinstance(i, int)]
        if not ids:
            return {}
        rows = self.env['ir.model.data'].sudo().search_read(
            [('model', '=', 'account.tax'), ('res_id', 'in', ids)], ['res_id', 'name'])
        return {row['res_id']: row['name'] for row in rows}

    def _mirak_ae_guess_category(self, external_name=''):
        """The PINT-AE category of this tax: from the UAE localization, Odoo's UBL code, the name, the rate."""
        self.ensure_one()
        for suffix, code in L10N_AE_TAXES:
            if suffix in external_name:
                return code
        ubl = 'ubl_cii_tax_category_code' in self._fields and self.ubl_cii_tax_category_code
        if ubl and UBL_CODES.get(ubl):
            return UBL_CODES[ubl]
        name = self.with_context(lang='en_US').name or ''
        for pattern, code in NAME_PATTERNS:
            if re.search(pattern, name, re.I):
                return code
        if self.amount_type == 'percent' and self.amount == 5:
            return 'S'
        if self.amount_type == 'percent' and self.amount == 0:
            return 'Z'
        return False
