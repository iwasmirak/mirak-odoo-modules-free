# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..tools import pint_ae

SCOPES = [
    ('b2b', 'B2B: business'),
    ('b2g', 'B2G: government'),
    ('b2c', 'B2C: consumer (not exchanged)'),
    ('none', 'Not subject to e-invoicing'),
]


class ResPartner(models.Model):
    _inherit = 'res.partner'

    mirak_ae_trn = fields.Char(string='TRN', compute='_compute_mirak_ae_trn', store=True, readonly=False,
                               help='Tax Registration Number of the customer or vendor (15 digits).')
    mirak_ae_endpoint_scheme = fields.Char(string='Peppol Scheme', default=pint_ae.UAE_EAS,
                                           help='0235 for UAE participants.')
    mirak_ae_endpoint_id = fields.Char(string='Peppol Participant ID', compute='_compute_mirak_ae_endpoint_id',
                                       store=True, readonly=False,
                                       help='For UAE businesses: the 10-digit TIN (first 10 digits of the TRN).')
    mirak_ae_legal_id = fields.Char(string='Legal Registration Number')
    mirak_ae_legal_id_type = fields.Selection(pint_ae.LEGAL_ID_TYPES, string='Legal Registration Type')
    mirak_ae_legal_authority = fields.Char(string='Issuing Authority')
    mirak_ae_emirate = fields.Selection(pint_ae.EMIRATES, string='Emirate')
    mirak_ae_scope = fields.Selection(SCOPES, string='E-Invoicing', compute='_compute_mirak_ae_scope', store=True,
                                      readonly=False)
    mirak_ae_peppol_status = fields.Selection([('unknown', 'Not checked'), ('registered', 'Registered on Peppol'),
                                               ('not_registered', 'Not registered')], default='unknown',
                                              string='Peppol Status', copy=False)
    mirak_ae_peppol_checked = fields.Datetime(string='Checked On', copy=False, readonly=True)

    @api.depends('vat', 'country_id')
    def _compute_mirak_ae_trn(self):
        for partner in self:
            trn = pint_ae.normalize_trn(partner.vat)
            if not partner.mirak_ae_trn and partner.country_id.code == 'AE' and len(trn) == pint_ae.TRN_LENGTH:
                partner.mirak_ae_trn = trn

    @api.depends('mirak_ae_trn')
    def _compute_mirak_ae_endpoint_id(self):
        for partner in self:
            tin = pint_ae.tin_from_trn(partner.mirak_ae_trn)
            if tin and not partner.mirak_ae_endpoint_id:
                partner.mirak_ae_endpoint_id = tin

    @api.depends('is_company', 'mirak_ae_trn', 'country_id')
    def _compute_mirak_ae_scope(self):
        for partner in self:
            if partner.mirak_ae_scope:
                continue
            partner.mirak_ae_scope = 'b2b' if (partner.is_company or partner.mirak_ae_trn) else 'b2c'

    @api.constrains('mirak_ae_trn')
    def _check_mirak_ae_trn(self):
        for partner in self:
            if partner.mirak_ae_trn and not pint_ae.is_valid_trn(partner.mirak_ae_trn):
                raise ValidationError(_('The TRN of %(partner)s must have 15 digits, start with 1 and end with 03 '
                                        '(now: "%(trn)s").',
                                        partner=partner.display_name, trn=partner.mirak_ae_trn))

    def write(self, vals):
        if 'mirak_ae_trn' in vals:
            vals['mirak_ae_trn'] = pint_ae.normalize_trn(vals['mirak_ae_trn']) or False
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('mirak_ae_trn'):
                vals['mirak_ae_trn'] = pint_ae.normalize_trn(vals['mirak_ae_trn'])
        return super().create(vals_list)

    def action_mirak_ae_check_peppol(self):
        """Ask the company's provider whether this partner can receive e-invoices on Peppol."""
        provider = self.env.company.mirak_ae_provider_id
        if not provider:
            raise UserError(_('Set your service provider in Settings > UAE E-Invoicing first.'))
        connector = provider._get_connector()
        for partner in self:
            if not partner.mirak_ae_endpoint_id:
                raise UserError(_('%s has no Peppol participant ID.', partner.display_name))
            result = connector.lookup_participant(partner.mirak_ae_endpoint_scheme or pint_ae.UAE_EAS,
                                                  partner.mirak_ae_endpoint_id)
            if result.status == 'unsupported':
                raise UserError(_('Your service provider does not offer a participant lookup.'))
            if not result.ok:
                raise UserError(_('The lookup failed: %s', result.message))
            partner.write({
                'mirak_ae_peppol_status': 'registered' if result.details.get('registered') else 'not_registered',
                'mirak_ae_peppol_checked': fields.Datetime.now(),
            })
        return True
