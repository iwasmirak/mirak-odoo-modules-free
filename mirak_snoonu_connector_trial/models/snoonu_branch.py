# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import fields, models


class SnoonuBranch(models.Model):
    _name = 'snoonu.branch'
    _description = 'Snoonu Branch'
    _order = 'sequence, id'

    account_id = fields.Many2one('snoonu.account', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='account_id.company_id', store=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    branch_ref = fields.Char(string='Snoonu Branch ID', required=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Stock From', required=True, check_company=True,
        help='The branch stock sent to Snoonu is the stock of this warehouse.')
    use_defaults = fields.Boolean(
        string='Use Brand Price', default=True,
        help='On: the branch inherits the brand Price and Discount (UseDefaults = true).\n'
             'Off: the branch sends its own Price and Discount (UseDefaults = false).')
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Branch Pricelist', check_company=True,
        help='Only used when "Use Brand Price" is off. Empty: the account pricelist.')
    stock_buffer = fields.Integer(
        string='Safety Stock', default=0,
        help='Units kept back: Snoonu receives the stock minus this number (never below zero).')

    _branch_uniq = models.Constraint('unique(account_id, branch_ref)',
                                     'This Snoonu branch is already configured on this account.')
