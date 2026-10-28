# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..tools import trial


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    mirak_ae_trial_html = fields.Html(compute='_compute_mirak_ae_trial_html', sanitize=False)

    @api.depends('company_id')
    def _compute_mirak_ae_trial_html(self):
        for settings in self:
            settings.mirak_ae_trial_html = trial.trial_banner(self.env, settings.company_id.id)

    mirak_ae_trn = fields.Char(related='company_id.mirak_ae_trn', readonly=False)
    mirak_ae_tin = fields.Char(related='company_id.mirak_ae_tin')
    mirak_ae_endpoint_id = fields.Char(related='company_id.mirak_ae_endpoint_id', readonly=False)
    mirak_ae_legal_id = fields.Char(related='company_id.mirak_ae_legal_id', readonly=False)
    mirak_ae_legal_id_type = fields.Selection(related='company_id.mirak_ae_legal_id_type', readonly=False)
    mirak_ae_legal_authority = fields.Char(related='company_id.mirak_ae_legal_authority', readonly=False)
    mirak_ae_emirate = fields.Selection(related='company_id.mirak_ae_emirate', readonly=False)
    mirak_ae_provider_id = fields.Many2one(related='company_id.mirak_ae_provider_id', readonly=False)
    mirak_ae_provider_environment = fields.Selection(related='company_id.mirak_ae_provider_id.environment')
    mirak_ae_auto_send = fields.Boolean(related='company_id.mirak_ae_auto_send', readonly=False)
    mirak_ae_mandate_phase = fields.Selection(related='company_id.mirak_ae_mandate_phase', readonly=False)
    mirak_ae_mandate_date = fields.Date(related='company_id.mirak_ae_mandate_date', readonly=False)
    mirak_ae_max_attempts = fields.Integer(related='company_id.mirak_ae_max_attempts', readonly=False)
    mirak_ae_days_left = fields.Integer(compute='_compute_mirak_ae_days_left')

    def _compute_mirak_ae_days_left(self):
        for settings in self:
            settings.mirak_ae_days_left = settings.company_id._mirak_ae_days_to_mandate() or 0

    def action_mirak_ae_test_connection(self):
        self.ensure_one()
        if not self.mirak_ae_provider_id:
            raise UserError(_('Choose your service provider first.'))
        return self.mirak_ae_provider_id.action_test_connection()
