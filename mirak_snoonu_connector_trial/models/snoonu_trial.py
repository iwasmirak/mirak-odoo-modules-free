# Copyright 2026 Bouchibane Karim
# License OPL-1 (18.0/legal/licenses.html)
"""Free trial edition of Mirak Connector for Snoonu.

No license key is needed, but the connector is limited in time and in volume:

* 1 Snoonu account (one brand / business unit),
* 25 products synchronized with Snoonu,
* 30 days, counted from the installation.

The limits are checked before every call to the Snoonu API, so the trial can be
evaluated with the real connector and the full feature set.
"""
from datetime import timedelta
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

TRIAL_ACCOUNTS = 1
TRIAL_PRODUCTS = 25
TRIAL_DAYS = 30
START_KEY = 'mirak_snoonu_trial.start'


class SnoonuAccountTrial(models.Model):
    _inherit = 'snoonu.account'

    trial_days_left = fields.Integer(compute='_compute_trial_days_left')
    trial_expiry = fields.Datetime(compute='_compute_trial_days_left')

    # ------------------------------------------------------------------ clock
    @api.model
    def _trial_start(self):
        param = self.env['ir.config_parameter'].sudo()
        value = param.get_param(START_KEY)
        if not value:
            value = fields.Datetime.to_string(fields.Datetime.now())
            param.set_param(START_KEY, value)
        return fields.Datetime.to_datetime(value)

    @api.model
    def _trial_end(self):
        return self._trial_start() + timedelta(days=TRIAL_DAYS)

    def _compute_trial_days_left(self):
        now = fields.Datetime.now()
        end = self._trial_end()
        left = max(math.ceil((end - now).total_seconds() / 86400), 0)
        for account in self:
            account.trial_expiry = end
            account.trial_days_left = left

    def _trial_check(self):
        if fields.Datetime.now() > self._trial_end():
            raise UserError(_(
                "The free trial of Mirak Connector for Snoonu has expired. "
                "Buy the full version to keep synchronizing your catalogue."))

    # ------------------------------------------------------------------ limits
    @api.model_create_multi
    def create(self, vals_list):
        if self.search_count([]) + len(vals_list) > TRIAL_ACCOUNTS:
            raise UserError(_(
                "The free trial manages %s Snoonu account. The full version supports "
                "as many brands / business units as you need.", TRIAL_ACCOUNTS))
        return super().create(vals_list)

    def _sync(self, mappings=None, force=False, trigger='manual'):
        self._trial_check()
        return super()._sync(mappings=mappings, force=force, trigger=trigger)


class SnoonuProductTrial(models.Model):
    _inherit = 'snoonu.product'

    @api.model_create_multi
    def create(self, vals_list):
        for account_id in {v.get('account_id') for v in vals_list if v.get('account_id')}:
            existing = self.search_count([('account_id', '=', account_id)])
            added = len([v for v in vals_list if v.get('account_id') == account_id])
            if existing + added > TRIAL_PRODUCTS:
                raise UserError(_(
                    "The free trial synchronizes %s products per account. "
                    "The full version has no limit.", TRIAL_PRODUCTS))
        return super().create(vals_list)
