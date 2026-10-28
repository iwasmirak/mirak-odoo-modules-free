# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Real-time updates: Shopify webhooks queue events that a quick synchronization handles within a minute."""
import base64
import hashlib
import hmac
import json
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

WEBHOOKS_QUERY = """
query MsWebhooks($uri: String) {
  webhookSubscriptions(first: 100, uri: $uri) { nodes { id topic uri } }
}
"""

WEBHOOK_CREATE_MUTATION = """
mutation MsWebhookCreate($topic: WebhookSubscriptionTopic!, $subscription: WebhookSubscriptionInput!) {
  webhookSubscriptionCreate(topic: $topic, webhookSubscription: $subscription) {
    webhookSubscription { id topic uri }
    userErrors { field message }
  }
}
"""

WEBHOOK_DELETE_MUTATION = """
mutation MsWebhookDelete($id: ID!) {
  webhookSubscriptionDelete(id: $id) {
    deletedWebhookSubscriptionId
    userErrors { field message }
  }
}
"""

# Shopify topic -> kind of work for the quick synchronization
WEBHOOK_TOPICS = {
    'ORDERS_CREATE': 'orders',
    'ORDERS_UPDATED': 'orders',
    'ORDERS_CANCELLED': 'orders',
    'REFUNDS_CREATE': 'orders',
    'PRODUCTS_CREATE': 'products',
    'PRODUCTS_UPDATE': 'products',
    'INVENTORY_LEVELS_UPDATE': 'stock',
    'CUSTOMERS_CREATE': 'customers',
    'CUSTOMERS_UPDATE': 'customers',
}


class MirakShopifyWebhookEvent(models.Model):
    """A notification received from Shopify, waiting for (or done by) the quick synchronization."""
    _name = 'mirak.shopify.webhook.event'
    _description = 'Shopify Webhook Event'
    _order = 'id desc'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    topic = fields.Char(required=True)
    kind = fields.Selection([('orders', 'Orders'), ('products', 'Products'), ('stock', 'Stock'),
                             ('customers', 'Customers'), ('other', 'Other')], required=True, default='other')
    webhook_id = fields.Char(index=True, help="X-Shopify-Webhook-Id, used to ignore duplicates")
    resource_gid = fields.Char(string='Shopify ID')
    processed = fields.Boolean(index=True)

    @api.autovacuum
    def _gc_processed(self):
        self.search([('processed', '=', True), ('create_date', '<', fields.Datetime.now() - timedelta(days=7))]).unlink()


class MirakShopifyStoreWebhook(models.Model):
    _inherit = 'mirak.shopify.store'

    webhook_enabled = fields.Boolean(string='Real-time Updates (Webhooks)', readonly=True, copy=False)
    webhook_base_url = fields.Char(
        string='Public Odoo URL',
        help="HTTPS address of this Odoo as seen from the internet (e.g. https://odoo.example.com). "
             "Shopify sends its notifications to it.")
    webhook_secret = fields.Char(
        string='Webhook Signing Secret', groups='base.group_system', copy=False,
        help="Secret used by Shopify to sign notifications: the app's Client secret (used when empty).")
    webhook_event_count = fields.Integer(compute='_compute_webhook_event_count')

    def _compute_webhook_event_count(self):
        Event = self.env['mirak.shopify.webhook.event']
        since = fields.Datetime.now() - timedelta(days=1)
        for store in self:
            store.webhook_event_count = Event.search_count([('store_id', '=', store.id), ('create_date', '>=', since)])

    def _webhook_uri(self):
        base = (self.webhook_base_url or self.env['ir.config_parameter'].sudo().get_param('web.base.url') or '').rstrip('/')
        if not base.startswith('https://'):
            raise UserError(self.env._(
                "Shopify only sends notifications to an HTTPS address. Set the Public Odoo URL "
                "(e.g. https://odoo.example.com) on the store."))
        return f"{base}/mirak_shopify/webhook/{self.id}"

    def action_register_webhooks(self):
        self.ensure_one()
        uri = self._webhook_uri()
        client = self._client()
        existing = {n['topic'] for n in client.query(WEBHOOKS_QUERY, {'uri': uri})['webhookSubscriptions']['nodes']}
        created = 0
        for topic in WEBHOOK_TOPICS:
            if topic in existing:
                continue
            client.mutate(WEBHOOK_CREATE_MUTATION, {'topic': topic, 'subscription': {'uri': uri, 'format': 'JSON'}},
                          'webhookSubscriptionCreate')
            created += 1
        self.webhook_enabled = True
        self._log('info', 'connection', f"Real-time updates on: {created} notification(s) registered ({uri}).")
        return self._notify(self.env._("Real-time updates activated (%s notifications).", len(WEBHOOK_TOPICS)))

    def action_unregister_webhooks(self):
        self.ensure_one()
        client = self._client()
        removed = 0
        try:
            uri = self._webhook_uri()
        except UserError:
            uri = None
        for node in client.query(WEBHOOKS_QUERY, {'uri': uri})['webhookSubscriptions']['nodes']:
            if f"/mirak_shopify/webhook/{self.id}" in (node.get('uri') or ''):
                client.mutate(WEBHOOK_DELETE_MUTATION, {'id': node['id']}, 'webhookSubscriptionDelete')
                removed += 1
        self.webhook_enabled = False
        self._log('info', 'connection', f"Real-time updates off: {removed} notification(s) removed.")
        return self._notify(self.env._("Real-time updates deactivated."), 'info')

    # ------------------------------------------------------------------
    # Reception (called by the controller, as superuser)
    # ------------------------------------------------------------------

    def _webhook_signature_valid(self, body, signature):
        secret = (self.sudo().webhook_secret or self.sudo().client_secret or '').strip()
        if not secret or not signature:
            return False
        digest = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
        return hmac.compare_digest(digest, signature.strip())

    def _webhook_receive(self, topic, body, signature, webhook_id=None):
        """Store a Shopify notification. Returns False when the signature is wrong."""
        self.ensure_one()
        if not self._webhook_signature_valid(body, signature):
            _logger.warning("Shopify [%s]: webhook with an invalid signature refused (%s)", self.name, topic)
            return False
        Event = self.env['mirak.shopify.webhook.event'].sudo()
        if webhook_id and Event.search_count([('store_id', '=', self.id), ('webhook_id', '=', webhook_id)]):
            return True  # Shopify retried a notification already received
        topic_key = (topic or '').upper().replace('/', '_')
        try:
            payload = json.loads(body or b'{}')
        except ValueError:
            payload = {}
        resource = payload.get('admin_graphql_api_id')
        if topic_key == 'INVENTORY_LEVELS_UPDATE' and payload.get('inventory_item_id'):
            resource = f"gid://shopify/InventoryItem/{payload['inventory_item_id']}"
        Event.create({
            'store_id': self.id, 'topic': topic_key, 'kind': WEBHOOK_TOPICS.get(topic_key, 'other'),
            'webhook_id': webhook_id, 'resource_gid': resource,
        })
        cron = self.env.ref('mirak_shopify_connector_trial.ir_cron_mirak_shopify_sync', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()
        return True

    # ------------------------------------------------------------------
    # Quick synchronization of the received events
    # ------------------------------------------------------------------

    def _run_webhook_sync(self):
        """Handle pending notifications only: changed products, orders, customers and stock levels."""
        self.ensure_one()
        Event = self.env['mirak.shopify.webhook.event']
        events = Event.search([('store_id', '=', self.id), ('processed', '=', False)], limit=1000)
        if not events:
            return
        cr = self.env.cr
        from .mirak_shopify_store import SYNC_LOCK_KEY
        cr.execute("SELECT pg_try_advisory_lock(%s)", (SYNC_LOCK_KEY + self.id,))
        if not cr.fetchone()[0]:
            return  # a synchronization is running; it will be handled next minute
        try:
            self._check_license()
            kinds = set(events.mapped('kind'))
            if 'products' in kinds and self.product_shopify_to_odoo:
                self._run_step(self._pull_products, 'product_in')
            if 'customers' in kinds and self.import_customers:
                self._run_step(self._pull_customers, 'customer_in')
            if 'orders' in kinds and self.import_orders:
                self._run_step(self._pull_orders, 'order_in')
                if self.invoice_trigger != 'none':
                    self._run_step(self._sync_accounting, 'invoice')
            if 'stock' in kinds and (self.stock_shopify_to_odoo or self.stock_odoo_to_shopify):
                items = [g for g in events.filtered(lambda e: e.kind == 'stock').mapped('resource_gid') if g]
                products = self.env['mirak.shopify.product.map'].search(
                    [('store_id', '=', self.id), ('inventory_item_gid', 'in', items)]).product_id
                if products:
                    self._run_step(lambda: self._sync_inventory(products), 'stock_out')
            events.write({'processed': True})
        except UserError as e:
            self._log('error', 'connection', f"{e}; real-time update skipped.")
            events.write({'processed': True})
        finally:
            cr.execute("SELECT pg_advisory_unlock(%s)", (SYNC_LOCK_KEY + self.id,))
