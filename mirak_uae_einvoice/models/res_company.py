# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

from ..tools import pint_ae

# Phased implementation (Ministerial Decision No. 244 of 2025). The exact day is the company's own date:
# these are only the defaults offered in the settings.
MANDATE_PHASES = [
    ('phase_1', 'Phase 1: revenue of AED 50 million or more (January 2027)'),
    ('phase_2', 'Phase 2: every other VAT-registered business (July 2027)'),
    ('custom', 'Another date'),
]
PHASE_DATES = {'phase_1': '2027-01-01', 'phase_2': '2027-07-01'}


class ResCompany(models.Model):
    _inherit = 'res.company'

    mirak_ae_trn = fields.Char(string='TRN', compute='_compute_mirak_ae_trn', store=True, readonly=False,
                               help='Tax Registration Number: 15 digits issued by the FTA. Taken from the Tax ID.')
    mirak_ae_tin = fields.Char(string='TIN', compute='_compute_mirak_ae_tin', store=True,
                               help='Tax Identification Number: the first 10 digits of the TRN.')
    mirak_ae_endpoint_id = fields.Char(string='Peppol Participant ID', compute='_compute_mirak_ae_endpoint_id',
                                       store=True, readonly=False,
                                       help='Given by your Accredited Service Provider at onboarding (0235 + TIN).')
    mirak_ae_legal_id = fields.Char(string='Legal Registration Number',
                                    help='Trade licence number (or Emirates ID / passport / cabinet decision).')
    mirak_ae_legal_id_type = fields.Selection(pint_ae.LEGAL_ID_TYPES, string='Legal Registration Type', default='TL')
    mirak_ae_legal_authority = fields.Char(string='Issuing Authority',
                                           help='e.g. "Dubai Department of Economy and Tourism". Required with a '
                                                'trade licence (rule ibr-101-ae).')
    mirak_ae_emirate = fields.Selection(pint_ae.EMIRATES, string='Emirate',
                                        help='Country subdivision of the address (rule ibr-128-ae).')
    mirak_ae_provider_id = fields.Many2one('mirak.ae.provider', string='Service Provider (ASP)',
                                           domain="['|', ('company_id', '=', False), ('company_id', '=', id)]")
    mirak_ae_auto_send = fields.Boolean(string='Send When Posted')
    mirak_ae_mandate_phase = fields.Selection(MANDATE_PHASES, string='Mandate Phase')
    mirak_ae_mandate_date = fields.Date(string='E-Invoicing Mandatory From',
                                        compute='_compute_mirak_ae_mandate_date', store=True, readonly=False)
    mirak_ae_max_attempts = fields.Integer(string='Automatic Retries', default=5)

    @api.depends('vat')
    def _compute_mirak_ae_trn(self):
        for company in self:
            trn = pint_ae.normalize_trn(company.vat)
            if not company.mirak_ae_trn and len(trn) == pint_ae.TRN_LENGTH:
                company.mirak_ae_trn = trn

    @api.depends('mirak_ae_trn')
    def _compute_mirak_ae_tin(self):
        for company in self:
            company.mirak_ae_tin = pint_ae.tin_from_trn(company.mirak_ae_trn)

    @api.depends('mirak_ae_tin')
    def _compute_mirak_ae_endpoint_id(self):
        for company in self:
            if company.mirak_ae_tin and not company.mirak_ae_endpoint_id:
                company.mirak_ae_endpoint_id = company.mirak_ae_tin

    @api.depends('mirak_ae_mandate_phase')
    def _compute_mirak_ae_mandate_date(self):
        for company in self:
            if company.mirak_ae_mandate_phase in PHASE_DATES:
                company.mirak_ae_mandate_date = fields.Date.to_date(PHASE_DATES[company.mirak_ae_mandate_phase])

    @api.constrains('mirak_ae_trn')
    def _check_mirak_ae_trn(self):
        for company in self:
            if company.mirak_ae_trn and not pint_ae.is_valid_trn(company.mirak_ae_trn):
                raise ValidationError(_('The TRN of %(company)s must have 15 digits, start with 1 and end with 03 '
                                        '(now: "%(trn)s").',
                                        company=company.name, trn=company.mirak_ae_trn))

    @api.constrains('mirak_ae_endpoint_id')
    def _check_mirak_ae_endpoint(self):
        for company in self:
            if company.mirak_ae_endpoint_id and not pint_ae.is_valid_endpoint(pint_ae.UAE_EAS,
                                                                              company.mirak_ae_endpoint_id):
                raise ValidationError(_('The Peppol participant ID of %s must be the 10-digit TIN '
                                        '(scheme 0235).', company.name))

    def write(self, vals):
        if 'mirak_ae_trn' in vals:
            vals['mirak_ae_trn'] = pint_ae.normalize_trn(vals['mirak_ae_trn']) or False
        return super().write(vals)

    def _mirak_ae_days_to_mandate(self):
        self.ensure_one()
        if not self.mirak_ae_mandate_date:
            return None
        return (self.mirak_ae_mandate_date - fields.Date.context_today(self)).days
