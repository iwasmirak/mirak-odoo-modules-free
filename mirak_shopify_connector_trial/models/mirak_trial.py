# Copyright 2026 Bouchibane Karim
# License OPL-1 (20.0/legal/licenses.html)
"""Free trial edition of Mirak Connector for Shopify.

No license key is needed, but the connector is limited in time and in volume:

* 25 linked products (product templates),
* 1 Shopify store,
* 30 days of synchronization, counted from the installation.

The limits are checked before every call to the Shopify API, so the trial can be
evaluated with the real connector and the full feature set.
"""
from datetime import timedelta
import math

from odoo import _, api, fields, models
from odoo.exceptions import UserError

TRIAL_PRODUCTS = 25
TRIAL_STORES = 1
TRIAL_DAYS = 30

START_PARAM = 'mirak_shopify_trial.start'
START_KEY = 'mirak_shopify_trial.start'


class MirakShopifyStoreTrial(models.Model):
    _inherit = 'mirak.shopify.store'

    trial_days_left = fields.Integer(compute='_compute_trial_days_left')
    trial_expiry = fields.Datetime(compute='_compute_trial_days_left')

    # ------------------------------------------------------------------
    # Trial clock
    # ------------------------------------------------------------------
    @api.model
    def _trial_start(self):
        """Installation date, stored in a system parameter so it survives upgrades."""
        param = self.env['ir.config_parameter'].sudo()
        value = param.get_str(START_KEY)
        if not value:
            value = fields.Datetime.to_string(fields.Datetime.now())
            param.set_str(START_KEY, value)
        return fields.Datetime.to_datetime(value)

    @api.model
    def _trial_end(self):
        return self._trial_start() + timedelta(days=TRIAL_DAYS)

    def _compute_trial_days_left(self):
        now = fields.Datetime.now()
        end = self._trial_end()
        # a started day counts as a full day: the trial shows 30 days on the first day
        left = max(math.ceil((end - now).total_seconds() / 86400), 0)
        for store in self:
            store.trial_expiry = end
            store.trial_days_left = left

    @api.depends('license_key', 'shop_domain')
    def _compute_license_status(self):
        super()._compute_license_status()
        for store in self:
            if store.trial_days_left:
                store.license_valid = True
                store.license_status = _("Free trial: %s days left", store.trial_days_left)
            else:
                store.license_valid = False
                store.license_status = _("Free trial expired")

    def _trial_expired(self):
        return fields.Datetime.now() > self._trial_end()

    def _trial_check(self):
        """Raise when the trial is over. Called before every call to the Shopify API."""
        if self._trial_expired():
            raise UserError(_(
                "The %(days)s-day free trial of Mirak Connector for Shopify is over. "
                "Buy the full version on the Odoo Apps Store to keep synchronizing %(store)s.",
                days=TRIAL_DAYS, store=self.name))

    def _check_license(self):
        self.ensure_one()
        self._trial_check()

    # ------------------------------------------------------------------
    # Volume limits
    # ------------------------------------------------------------------
    def _trial_products_used(self):
        """Number of product templates already linked to this store."""
        self.ensure_one()
        return len(self.env['mirak.shopify.product.map'].search(
            [('store_id', '=', self.id)]).product_tmpl_id)

    def _trial_product_limit_reached(self, template=None):
        used = self._trial_products_used()
        if template is not None:
            Map = self.env['mirak.shopify.product.map']
            if Map.search_count([('store_id', '=', self.id),
                                 ('product_tmpl_id', '=', template.id)]):
                return False    # already linked: it keeps synchronizing
        return used >= TRIAL_PRODUCTS

    @api.model_create_multi
    def create(self, vals_list):
        if self.search_count([]) + len(vals_list) > TRIAL_STORES:
            raise UserError(_(
                "The free trial of Mirak Connector for Shopify works with a single Shopify store. "
                "Buy the full version on the Odoo Apps Store to connect several stores."))
        return super().create(vals_list)

    # ---- Shopify -> Odoo ---------------------------------------------
    def _import_product(self, node):
        if self._trial_product_limit_reached() and not self.env[
                'mirak.shopify.product.map'].search_count([
                    ('store_id', '=', self.id), ('shopify_product_gid', '=', node.get('id'))]):
            self._log('warning', 'product_in', _(
                "%(product)s: the free trial is limited to %(limit)s products, this one is skipped.",
                product=node.get('title') or node.get('id'), limit=TRIAL_PRODUCTS),
                reference=node.get('id'))
            return False
        return super()._import_product(node)

    # ---- Odoo -> Shopify ---------------------------------------------
    def _export_template(self, template):
        if self._trial_product_limit_reached(template):
            self._log('warning', 'product_out', _(
                "%(product)s: the free trial is limited to %(limit)s products, this one is not sent "
                "to Shopify.", product=template.display_name, limit=TRIAL_PRODUCTS),
                template=template)
            return
        return super()._export_template(template)


class MirakShopifyProductMapTrial(models.Model):
    _inherit = 'mirak.shopify.product.map'

    @api.model_create_multi
    def create(self, vals_list):
        # Last line of defence: no code path may exceed the trial volume
        Store = self.env['mirak.shopify.store']
        for vals in vals_list:
            store = Store.browse(vals['store_id'])
            if store._trial_product_limit_reached():
                raise UserError(_(
                    "The free trial of Mirak Connector for Shopify is limited to %s linked products. "
                    "Buy the full version on the Odoo Apps Store to synchronize your whole catalogue.",
                    TRIAL_PRODUCTS))
        return super().create(vals_list)
