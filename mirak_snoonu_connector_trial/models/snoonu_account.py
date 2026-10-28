# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
import hashlib
import json
import logging
import math
import time
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_round

_logger = logging.getLogger(__name__)

STAGING_URL = 'https://partnersintegration.qat.stg.snoonu.com'
BULK_PATH = '/api/v1.0/Product/{business_unit_id}/BulkUpdate'
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3


class SnoonuAccount(models.Model):
    _name = 'snoonu.account'
    _description = 'Snoonu Account'
    _inherit = ['mail.thread']
    _order = 'sequence, id'

    name = fields.Char(required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')

    # --- connection
    environment = fields.Selection(
        [('staging', 'Staging (Sandbox)'), ('production', 'Production')],
        default='staging', required=True, tracking=True)
    staging_url = fields.Char(string='Staging URL', default=STAGING_URL, required=True)
    production_url = fields.Char(
        string='Production URL',
        help='Given by Snoonu after a successful staging validation. '
             'The host is enough (https://...); a full .../BulkUpdate address also works.')
    business_unit_id = fields.Char(string='Business Unit ID', required=True, tracking=True,
                                   help='Your brand ID within Snoonu.')
    api_email = fields.Char(string='Registered Email', groups='mirak_snoonu_connector_trial.group_snoonu_manager',
                            copy=False)
    api_key = fields.Char(string='API Key', groups='mirak_snoonu_connector_trial.group_snoonu_manager', copy=False)
    timeout = fields.Integer(string='Timeout (seconds)', default=60)
    batch_size = fields.Integer(string='Products per Request', default=500,
                                help='Snoonu accepts thousands of SKUs per request; smaller batches '
                                     'isolate errors better.')

    # --- what is sent
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', check_company=True,
        help='Source of the Snoonu price. Empty: the product Sales Price.')
    discount_mode = fields.Selection(
        [('none', 'No discount'),
         ('pricelist', 'Pricelist reduction sent as discount')],
        default='none', required=True,
        help='No discount: Price = pricelist price (or Sales Price).\n'
             'Pricelist reduction: Price = Sales Price and Discount = Sales Price - pricelist price, '
             'so Snoonu shows the promotion. Snoonu discounts are amounts, never percentages.')
    send_brand_price = fields.Boolean(
        string='Send Brand Price', default=True,
        help='Send the brand-level Price and Discount, used by every branch set to "Use Brand Price".')
    stock_field = fields.Selection(
        [('free_qty', 'Available (on hand - reserved)'),
         ('qty_available', 'On hand'),
         ('virtual_available', 'Forecasted')],
        string='Stock Quantity', default='free_qty', required=True)

    # --- automation
    auto_sync = fields.Boolean(string='Automatic Sync', tracking=True)
    sync_interval = fields.Integer(string='Every (minutes)', default=15)
    last_sync_at = fields.Datetime(string='Last Sync', readonly=True, copy=False)

    branch_ids = fields.One2many('snoonu.branch', 'account_id', string='Branches')
    product_ids = fields.One2many('snoonu.product', 'account_id', string='Products')
    log_ids = fields.One2many('snoonu.log', 'account_id', string='Logs')
    product_count = fields.Integer(compute='_compute_counts')
    synced_count = fields.Integer(compute='_compute_counts')
    pending_count = fields.Integer(compute='_compute_counts')
    error_count = fields.Integer(compute='_compute_counts')
    log_count = fields.Integer(compute='_compute_counts')
    endpoint_url = fields.Char(compute='_compute_endpoint_url')

    _business_unit_uniq = models.Constraint(
        'unique(business_unit_id, environment, company_id)',
        'This Snoonu business unit is already configured for this environment.')

    @api.depends('product_ids.state', 'log_ids')
    def _compute_counts(self):
        Product = self.env['snoonu.product']
        groups = Product._read_group([('account_id', 'in', self.ids)], ['account_id', 'state'], ['__count'])
        counts = {}
        for account, state, count in groups:
            counts.setdefault(account.id, {})[state] = count
        log_counts = dict(self.env['snoonu.log']._read_group(
            [('account_id', 'in', self.ids)], ['account_id'], ['__count']))
        for account in self:
            c = counts.get(account.id, {})
            account.synced_count = c.get('synced', 0)
            account.pending_count = c.get('pending', 0)
            account.error_count = c.get('error', 0)
            account.product_count = sum(c.values())
            account.log_count = log_counts.get(account, 0)

    @api.depends('environment', 'staging_url', 'production_url', 'business_unit_id')
    def _compute_endpoint_url(self):
        for account in self:
            account.endpoint_url = account._get_endpoint_url(raise_if_missing=False)

    @api.constrains('batch_size', 'sync_interval', 'timeout')
    def _check_numbers(self):
        for account in self:
            if account.batch_size < 1 or account.sync_interval < 1 or account.timeout < 5:
                raise ValidationError(_('Batch size and interval must be positive and the timeout at least 5 seconds.'))

    # ------------------------------------------------------------------ cron switch
    @api.model_create_multi
    def create(self, vals_list):
        accounts = super().create(vals_list)
        accounts._update_cron()
        return accounts

    def write(self, vals):
        res = super().write(vals)
        if 'auto_sync' in vals or 'active' in vals:
            self._update_cron()
        return res

    def _update_cron(self):
        cron = self.env.ref('mirak_snoonu_connector_trial.ir_cron_snoonu_sync', raise_if_not_found=False)
        if cron:
            needed = bool(self.sudo().search_count([('auto_sync', '=', True)]))
            if cron.sudo().active != needed:
                cron.sudo().active = needed

    # ------------------------------------------------------------------ helpers
    def _get_endpoint_url(self, raise_if_missing=True):
        self.ensure_one()
        base = (self.production_url if self.environment == 'production' else self.staging_url) or ''
        base = base.strip()
        if not base or not self.business_unit_id:
            if raise_if_missing:
                raise UserError(_('Set the %(env)s URL and the Business Unit ID first.',
                                  env=dict(self._fields['environment'].selection)[self.environment]))
            return False
        if 'BulkUpdate' in base:
            return base.replace('{businessUnitId}', self.business_unit_id) \
                       .replace('{YOUR_BUSINESS_UNIT_ID}', self.business_unit_id)
        return base.rstrip('/') + BULK_PATH.format(business_unit_id=self.business_unit_id)

    def _money(self, amount):
        return float_round(amount or 0.0, precision_digits=self.currency_id.decimal_places or 2)

    def _price_and_discount(self, product, pricelist):
        """(price, discount) for one variant: Snoonu expects the discount as an amount."""
        self.ensure_one()
        sale_price = product.lst_price
        if not pricelist:
            return self._money(sale_price), 0.0
        pricelist_price = pricelist._get_product_price(product, 1.0, currency=self.currency_id)
        if self.discount_mode == 'pricelist':
            return self._money(sale_price), self._money(max(sale_price - pricelist_price, 0.0))
        return self._money(pricelist_price), 0.0

    def _stock_by_branch(self, products):
        """{branch.id: {product.id: int quantity}} read in one pass per warehouse."""
        self.ensure_one()
        result = {}
        # stock moves done earlier in the same transaction must be seen
        products.invalidate_recordset([self.stock_field])
        for branch in self.branch_ids.filtered('active'):
            ctx_products = products.with_context(warehouse_id=branch.warehouse_id.id)
            result[branch.id] = {
                p.id: max(int(math.floor(p[self.stock_field] or 0.0)) - branch.stock_buffer, 0)
                for p in ctx_products
            }
        return result

    def _build_documents(self, mappings):
        """[(mapping, document, hash)] for the given snoonu.product records."""
        self.ensure_one()
        branches = self.branch_ids.filtered('active')
        if not branches:
            raise UserError(_('Add at least one active branch to %s.', self.name))
        mappings = mappings.filtered(lambda m: m.product_id)
        products = mappings.product_id.with_company(self.company_id)
        stock = self._stock_by_branch(products)
        result = []
        for mapping in mappings:
            product = mapping.product_id.with_company(self.company_id)
            available = bool(mapping.is_available and product.active and product.sale_ok)
            doc = {'BusinessUnitId': self.business_unit_id, 'SKU': mapping.sku}
            if self.send_brand_price:
                doc['Price'], doc['Discount'] = self._price_and_discount(product, self.pricelist_id)
            configs = []
            for branch in branches:
                config = {
                    'BranchId': branch.branch_ref,
                    'UseDefaults': branch.use_defaults,
                    'IsAvailable': available,
                    'Stock': stock[branch.id].get(product.id, 0),
                }
                if not branch.use_defaults:
                    config['Price'], config['Discount'] = self._price_and_discount(
                        product, branch.pricelist_id or self.pricelist_id)
                configs.append(config)
            doc['BranchConfigurations'] = configs
            digest = hashlib.sha1(json.dumps(doc, sort_keys=True).encode()).hexdigest()
            result.append((mapping, doc, digest))
        return result

    def _payload(self, documents):
        return {'Filters': ['SKU'], 'Documents': documents}

    def _send(self, documents):
        """PATCH one batch. Returns (ok, http_status, response_text, seconds)."""
        self.ensure_one()
        account = self.sudo()
        if not account.api_key or not account.api_email:
            raise UserError(_('Enter the API Key and the Registered Email of %s.', self.name))
        url = account._get_endpoint_url()
        headers = {
            'X-AUTH-APIKEY': account.api_key,
            'X-Email': account.api_email,
            'accept': 'application/json',
            'content-type': 'application/json',
        }
        body = json.dumps(self._payload(documents))
        status, text, started = 0, '', time.monotonic()
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.monotonic()
            try:
                response = requests.patch(url, data=body, headers=headers, timeout=account.timeout)
                status, text = response.status_code, response.text or ''
            except requests.RequestException as e:
                status, text = 0, str(e)
            if status and status not in RETRY_STATUSES:
                break
            if attempt < MAX_ATTEMPTS:
                _logger.info('Snoonu %s: attempt %s failed (%s), retrying', self.name, attempt, status or text)
                time.sleep(2 ** attempt)
        return 200 <= status < 300, status, text, time.monotonic() - started

    # ------------------------------------------------------------------ sync
    def _sync(self, mappings=None, force=False, trigger='manual'):
        """Send changed (or all, with force) products. Returns a summary dict."""
        self.ensure_one()
        mappings = (mappings or self.product_ids).filtered(lambda m: m.active and m.account_id == self)
        built = self._build_documents(mappings)
        if not force:
            built = [b for b in built if b[2] != b[0].last_hash or b[0].state != 'synced']
        summary = {'sent': 0, 'ok': 0, 'failed': 0, 'skipped': len(mappings) - len(built)}
        size = max(self.batch_size, 1)
        for start in range(0, len(built), size):
            chunk = built[start:start + size]
            documents = [doc for _m, doc, _h in chunk]
            ok, status, text, seconds = self._send(documents)
            now = fields.Datetime.now()
            self.env['snoonu.log'].sudo().create({
                'account_id': self.id,
                'trigger': trigger,
                'sku_count': len(chunk),
                'state': 'success' if ok else 'error',
                'http_status': status,
                'duration': seconds,
                'request': json.dumps(self._payload(documents), indent=1, ensure_ascii=False),
                'response': text,
            })
            summary['sent'] += len(chunk)
            for mapping, doc, digest in chunk:
                if ok:
                    mapping.write({'state': 'synced', 'last_hash': digest, 'last_sync_at': now,
                                   'last_error': False, 'last_document': json.dumps(doc, indent=1)})
                else:
                    mapping.write({'state': 'error', 'last_error': f'HTTP {status}: {text[:2000]}',
                                   'last_document': json.dumps(doc, indent=1)})
            summary['ok' if ok else 'failed'] += len(chunk)
            if trigger == 'cron':
                self.env.cr.commit()
        self.last_sync_at = fields.Datetime.now()
        return summary

    def _notify_summary(self, summary):
        if summary['failed']:
            kind, title = 'danger', _('Snoonu: some products failed')
        elif summary['sent']:
            kind, title = 'success', _('Snoonu updated')
        else:
            kind, title = 'info', _('Snoonu is already up to date')
        message = _('%(ok)s sent, %(failed)s failed, %(skipped)s unchanged.', **summary)
        return {'type': 'ir.actions.client', 'tag': 'display_notification',
                'params': {'title': title, 'message': message, 'type': kind, 'sticky': bool(summary['failed']),
                           'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'}}}

    @api.model
    def _cron_sync(self):
        now = fields.Datetime.now()
        for account in self.search([('auto_sync', '=', True)]):
            if account.last_sync_at and account.last_sync_at + timedelta(minutes=account.sync_interval) > now:
                continue
            try:
                account._sync(trigger='cron')
                self.env.cr.commit()
            except Exception as e:  # one failing account must not block the others
                self.env.cr.rollback()
                _logger.exception('Snoonu automatic sync failed for %s', account.name)
                account.message_post(body=_('Automatic sync failed: %s', e))
                self.env.cr.commit()

    # ------------------------------------------------------------------ buttons
    def action_sync_changes(self):
        self.ensure_one()
        return self._notify_summary(self._sync())

    def action_sync_all(self):
        self.ensure_one()
        return self._notify_summary(self._sync(force=True))

    def action_send_test(self):
        """Send a single product: the quickest staging check."""
        self.ensure_one()
        mapping = self.product_ids.filtered('active')[:1]
        if not mapping:
            raise UserError(_('Add at least one product first.'))
        return self._notify_summary(self._sync(mapping, force=True, trigger='test'))

    def action_preview_payload(self):
        self.ensure_one()
        mappings = self.product_ids.filtered('active')[:20]
        if not mappings:
            raise UserError(_('Add at least one product first.'))
        documents = [doc for _m, doc, _h in self._build_documents(mappings)]
        preview = self.env['snoonu.payload.preview'].create({
            'account_id': self.id,
            'endpoint_url': self._get_endpoint_url(raise_if_missing=False),
            'payload': json.dumps(self._payload(documents), indent=2, ensure_ascii=False),
            'product_count': len(self.product_ids.filtered('active')),
        })
        return {'type': 'ir.actions.act_window', 'res_model': 'snoonu.payload.preview', 'res_id': preview.id,
                'view_mode': 'form', 'target': 'new', 'name': _('Payload Preview')}

    def action_add_products(self):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': 'snoonu.add.products', 'view_mode': 'form',
                'target': 'new', 'name': _('Add Products to Snoonu'),
                'context': {'default_account_id': self.id}}

    def _open(self, model, name, extra_domain=None):
        self.ensure_one()
        return {'type': 'ir.actions.act_window', 'res_model': model, 'name': name, 'view_mode': 'list,form',
                'domain': [('account_id', '=', self.id)] + (extra_domain or []),
                'context': {'default_account_id': self.id}}

    def action_open_products(self):
        return self._open('snoonu.product', _('Snoonu Products'))

    def action_open_errors(self):
        return self._open('snoonu.product', _('Products in Error'), [('state', '=', 'error')])

    def action_open_logs(self):
        return self._open('snoonu.log', _('Snoonu Logs'))
