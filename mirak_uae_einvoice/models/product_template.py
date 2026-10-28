# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import fields, models

from ..tools import pint_ae


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    mirak_ae_item_type = fields.Selection(
        pint_ae.ITEM_TYPES, string='E-Invoice Item Type',
        help='BTAE-13. Goods need an HS code, services a service accounting code, "Both" needs the two '
             '(rules ibr-184-ae to ibr-186-ae). Leave empty to omit the item type.')
    mirak_ae_hs_code = fields.Char(string='HS Code', help='Harmonized System code (ibt-158, scheme HS).')
    mirak_ae_sac_code = fields.Char(string='Service Accounting Code', help='BTAE-17, scheme SAC.')
    mirak_ae_goods_type = fields.Selection(
        pint_ae.GOODS_TYPES, string='Reverse Charge Goods Type',
        help='BTAE-09. Required when this product is sold under the domestic reverse charge (rule ibr-166-ae).')
