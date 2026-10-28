# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Abandoned Shopify checkouts become CRM opportunities (when the CRM app is installed); an
opportunity is marked won when the customer completes the checkout later."""
import logging
from datetime import timedelta

from odoo import _, fields, models

_logger = logging.getLogger(__name__)

CHECKOUTS_QUERY = """
query MsCheckouts($cursor: String, $query: String) {
  abandonedCheckouts(first: 50, after: $cursor, query: $query) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      name
      createdAt
      updatedAt
      completedAt
      abandonedCheckoutUrl
      totalPriceSet { shopMoney { amount currencyCode } }
      customer { id firstName lastName defaultEmailAddress { emailAddress } defaultPhoneNumber { phoneNumber } }
      lineItems(first: 20) { nodes { title variantTitle quantity } }
    }
  }
}
"""

SHOPIFY_DT_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


class MirakShopifyCheckout(models.Model):
    _name = 'mirak.shopify.checkout'
    _description = 'Shopify Abandoned Checkout'
    _order = 'id desc'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    gid = fields.Char(string='Shopify Checkout ID', required=True)
    name = fields.Char()
    partner_id = fields.Many2one('res.partner', ondelete='set null')
    amount = fields.Float()
    url = fields.Char(string='Recovery Link')
    lead_res_id = fields.Integer(string='Opportunity ID')
    completed = fields.Boolean()

    _sql_constraints = [('_store_gid_uniq', 'unique(store_id, gid)', 'This checkout is already imported.')]


class MirakShopifyStoreCheckout(models.Model):
    _inherit = 'mirak.shopify.store'

    import_abandoned_checkouts = fields.Boolean(
        string='Abandoned Checkouts → CRM', default=False,
        help="Each abandoned Shopify checkout of a known customer becomes a CRM opportunity with the recovery "
             "link; it is marked won if the customer completes the order. Needs the CRM app.")
    checkout_team_id = fields.Many2one('crm.team', string='Sales Team of the Opportunities', check_company=True)
    last_checkout_pull = fields.Datetime(readonly=True, copy=False)

    def _pull_abandoned_checkouts(self):
        if 'crm.lead' not in self.env:
            self._log('warning', 'order_in', "Abandoned checkouts need the CRM app: install it or untick the option.")
            return
        client = self._client()
        started = fields.Datetime.now()
        since = self.last_checkout_pull - timedelta(minutes=2) if self.last_checkout_pull \
            else started - timedelta(days=7)
        search = f"updated_at:>'{since.strftime(SHOPIFY_DT_FORMAT)}'"
        created = won = 0
        for node in client.paginate(CHECKOUTS_QUERY, {'query': search}, 'abandonedCheckouts'):
            try:
                with self.env.cr.savepoint():
                    result = self._import_checkout(node)
                    created += result == 'created'
                    won += result == 'won'
            except Exception as e:
                _logger.exception("Shopify checkout %s failed", node.get('id'))
                self._log('error', 'order_in', f"Abandoned checkout {node.get('name')}: {e}", reference=node.get('id'))
        self.last_checkout_pull = started
        if created or won:
            self._log('info', 'order_in', f"Abandoned checkouts: {created} opportunity(ies) created, {won} won")

    def _import_checkout(self, node):
        Checkout = self.env['mirak.shopify.checkout']
        Lead = self.env['crm.lead']
        record = Checkout.search([('store_id', '=', self.id), ('gid', '=', node['id'])], limit=1)
        if record:
            if node.get('completedAt') and not record.completed:
                record.completed = True
                lead = Lead.browse(record.lead_res_id).exists()
                if lead and lead.active and not lead.probability == 100:
                    lead.action_set_won()
                    return 'won'
            return None
        if node.get('completedAt'):
            return None  # bought in the end: nothing to follow up
        customer = node.get('customer') or {}
        email, phone = self._customer_contact(customer)
        if not (email or phone):
            return None  # nobody to contact
        partner = self._partner_for_customer(customer.get('id')) or self._find_partner(email, phone)
        name = f"{customer.get('firstName') or ''} {customer.get('lastName') or ''}".strip() or email or phone
        amount = float(((node.get('totalPriceSet') or {}).get('shopMoney') or {}).get('amount') or 0)
        items = "\n".join(f"- {i['quantity']} × {i['title']}" + (f" ({i['variantTitle']})" if i.get('variantTitle') else '')
                          for i in (node.get('lineItems') or {}).get('nodes', []))
        lead = Lead.create({
            'name': _("Abandoned checkout %(name)s - %(customer)s", name=node.get('name') or '', customer=name),
            'type': 'opportunity',
            'partner_id': partner.id or False,
            'contact_name': False if partner else name,
            'email_from': email or False,
            'phone': phone or False,
            'expected_revenue': amount,
            'team_id': self.checkout_team_id.id or False,
            'description': self._text_to_html(
                f"{items}\n\nRecovery link: {node.get('abandonedCheckoutUrl') or ''}"),
        })
        Checkout.create({'store_id': self.id, 'gid': node['id'], 'name': node.get('name'),
                         'partner_id': partner.id or False, 'amount': amount,
                         'url': node.get('abandonedCheckoutUrl'), 'lead_res_id': lead.id})
        return 'created'
