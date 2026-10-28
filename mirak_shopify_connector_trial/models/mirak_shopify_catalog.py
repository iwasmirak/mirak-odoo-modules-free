# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Catalogue and customer features of the store: compare-at prices, tags, product types and
collections, customers in both directions, and orders cancelled from Odoo."""
import logging
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .shopify_client import ShopifyError

_logger = logging.getLogger(__name__)

PRODUCT_TAGS_QUERY = """
query MsProductTags($id: ID!) { product(id: $id) { id tags } }
"""

COLLECTION_FIND_QUERY = """
query MsCollectionFind($query: String!) {
  collections(first: 10, query: $query) { nodes { id title ruleSet { appliedDisjunctively } } }
}
"""

COLLECTION_CREATE_MUTATION = """
mutation MsCollectionCreate($collection: CollectionCreateInput!) {
  collectionCreate(collection: $collection) {
    collection { id title }
    userErrors { field message }
  }
}
"""

CUSTOMERS_QUERY = """
query MsCustomers($cursor: String, $query: String) {
  customers(first: 50, after: $cursor, query: $query, sortKey: UPDATED_AT) {
    pageInfo { hasNextPage endCursor }
    nodes {
      id
      updatedAt
      firstName
      lastName
      note
      defaultEmailAddress { emailAddress }
      defaultPhoneNumber { phoneNumber }
      defaultAddress { name company address1 address2 city zip provinceCode countryCodeV2 phone }
    }
  }
}
"""

CUSTOMER_SET_MUTATION = """
mutation MsCustomerSet($input: CustomerSetInput!, $identifier: CustomerSetIdentifiers) {
  customerSet(input: $input, identifier: $identifier) {
    customer { id updatedAt }
    userErrors { field message code }
  }
}
"""

ORDER_CANCEL_MUTATION = """
mutation MsOrderCancel($orderId: ID!, $restock: Boolean!, $notify: Boolean, $refund: OrderCancelRefundMethodInput,
                       $note: String) {
  orderCancel(orderId: $orderId, reason: OTHER, restock: $restock, notifyCustomer: $notify,
              refundMethod: $refund, staffNote: $note) {
    job { id }
    orderCancelUserErrors { field message code }
  }
}
"""

SHOPIFY_DT_FORMAT = '%Y-%m-%dT%H:%M:%SZ'


class MirakShopifyCollection(models.Model):
    """Shopify collection holding the products of one Odoo category."""
    _name = 'mirak.shopify.collection'
    _description = 'Shopify Collection of a Category'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    categ_id = fields.Many2one('product.category', required=True, ondelete='cascade')
    gid = fields.Char(string='Shopify Collection ID', required=True)
    name = fields.Char()

    _store_categ_uniq = models.Constraint('unique(store_id, categ_id)',
                                          'This category already has a collection in this store.')


class MirakShopifyCustomer(models.Model):
    """Link between an Odoo contact and a Shopify customer."""
    _name = 'mirak.shopify.customer'
    _description = 'Shopify Customer Link'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    customer_gid = fields.Char(string='Shopify Customer ID', required=True, index=True)
    shopify_updated_at = fields.Char()

    # A contact may match several Shopify customers (e.g. guest checkouts with the same email)
    _gid_uniq = models.Constraint('unique(store_id, customer_gid)', 'This Shopify customer is already linked.')


class MirakShopifyStoreCatalog(models.Model):
    _inherit = 'mirak.shopify.store'

    # Prices
    compare_at_source = fields.Selection([
        ('none', 'Not managed from Odoo'),
        ('list_price', 'Sales price, when higher than the Shopify price'),
        ('pricelist', 'Price of a pricelist, when higher than the Shopify price'),
    ], string='Compare-at Price', default='none', required=True,
        help="Shopify shows the compare-at price crossed out next to the price. 'Sales price' needs the "
             "Shopify price to come from a pricelist (your promotional prices). Sent with the prices.")
    compare_at_pricelist_id = fields.Many2one('product.pricelist', string='Compare-at Pricelist', check_company=True)

    # Tags, product type, collections
    sync_tags = fields.Boolean(
        string='Tags', default=False,
        help="Odoo product tags ↔ Shopify tags. Shopify tags are merged into Odoo before Odoo first "
             "sends the tags of a product, so no tag used by your collections is lost.")
    category_sync = fields.Selection([
        ('none', 'No'),
        ('odoo', 'Odoo category → Shopify product type'),
        ('both', 'Odoo category ↔ Shopify product type'),
    ], string='Product Category', default='none', required=True,
        help="Shopify product types become Odoo categories (created when missing) only in the two-way mode.")
    shopify_categ_parent_id = fields.Many2one(
        'product.category', string='Parent of New Categories',
        help="Categories created from Shopify product types are placed under this category.")
    category_collections = fields.Boolean(
        string='Category Collections', default=False,
        help="Add products to a manual Shopify collection named like their Odoo category (created when "
             "missing); a product changing category moves to the new collection.")

    # Stock of several warehouses published at the main location
    main_extra_warehouse_ids = fields.Many2many(
        related='location_id.extra_warehouse_ids', readonly=False, string='Also Publish Stock Of',
        help="The stock of these warehouses is added to the quantity published at the main location.")

    # Customers
    import_customers = fields.Boolean(
        string='Import Shopify Customers', default=False,
        help="Create and update Odoo contacts from all Shopify customers, not only those who ordered.")
    last_customer_pull = fields.Datetime(readonly=True, copy=False)

    # Point of sale orders
    pos_orders = fields.Selection([
        ('import', 'Import them, already delivered'),
        ('skip', 'Do not import them'),
    ], string='Shopify POS Orders', default='import', required=True,
        help="Orders sold with Shopify POS are imported with their shop location and their delivery is "
             "validated at once (the customer left with the goods).")

    # Orders cancelled in Odoo
    cancel_in_shopify = fields.Boolean(
        string='Cancel in Shopify Orders Cancelled in Odoo', default=False,
        help="An imported order cancelled in Odoo (nothing delivered) is cancelled in Shopify too.")
    cancel_restock = fields.Boolean(string='Restock in Shopify', default=True)
    cancel_refund = fields.Boolean(
        string='Refund the Customer', default=False,
        help="Refund the amount paid to the original payment method when cancelling in Shopify.")
    cancel_notify = fields.Boolean(string='Notify the Customer', default=False)

    @api.constrains('compare_at_source', 'compare_at_pricelist_id', 'price_source')
    def _check_compare_at(self):
        for store in self:
            if store.compare_at_source == 'pricelist' and not store.compare_at_pricelist_id:
                raise ValidationError(self.env._("Select the pricelist of the compare-at prices."))
            if store.compare_at_source == 'list_price' and store.price_source != 'pricelist':
                raise ValidationError(self.env._(
                    "A compare-at price from the sales price needs the Shopify price to come from a pricelist "
                    "(Products tab, Shopify Price)."))

    # ------------------------------------------------------------------
    # Compare-at price
    # ------------------------------------------------------------------

    def _variant_compare_at(self, product, price):
        """Compare-at price to send with `price` (Shopify currency): a money string, None to clear it,
        or False when Odoo does not manage it."""
        if self.compare_at_source == 'none':
            return False
        if self.compare_at_source == 'pricelist' and self.compare_at_pricelist_id:
            pricelist = self.compare_at_pricelist_id
            compare = self._convert_price(pricelist._get_product_price(product, 1.0), pricelist.currency_id,
                                          self._shop_currency())
        else:
            compare = self._convert_price(product.lst_price, self.company_id.currency_id, self._shop_currency())
        return self._money(compare) if compare > price + 0.005 else None

    # ------------------------------------------------------------------
    # Tags, product type and collections
    # ------------------------------------------------------------------

    def _odoo_tags(self, names):
        Tag = self.env['product.tag']
        tags = Tag
        for name in {n.strip() for n in names if n and n.strip()}:
            tag = Tag.search([('name', '=ilike', name)], limit=1) or Tag.create({'name': name})
            tags |= tag
        return tags

    def _tags_for_push(self, template, product_gid=None):
        """Tags to send for a product. The first time, Shopify's tags are merged into Odoo first."""
        if product_gid and not template.ms_tags_synced:
            node = self._client().query(PRODUCT_TAGS_QUERY, {'id': product_gid}).get('product') or {}
            shopify_tags = self._odoo_tags(node.get('tags') or [])
            template.with_context(ms_no_export=True).write({
                'product_tag_ids': [(4, t.id) for t in shopify_tags], 'ms_tags_synced': True})
        return sorted(template.product_tag_ids.mapped('name'))

    def _apply_shopify_tags(self, template, names):
        tags = self._odoo_tags(names)
        vals = {'ms_tags_synced': True}
        if tags != template.product_tag_ids:
            vals['product_tag_ids'] = [(6, 0, tags.ids)]
        template.with_context(ms_no_export=True).write(vals)

    def _shopify_product_type(self, template):
        return template.categ_id.name or ''

    def _apply_shopify_product_type(self, template, product_type):
        """Two-way category mode: the Shopify product type gives the Odoo category."""
        product_type = (product_type or '').strip()
        if self.category_sync != 'both' or not product_type or template.categ_id.name == product_type:
            return
        Category = self.env['product.category']
        parent = self.shopify_categ_parent_id
        categ = Category.search([('name', '=ilike', product_type), ('parent_id', '=', parent.id or False)], limit=1) \
            or Category.search([('name', '=ilike', product_type)], limit=1)
        if not categ:
            if not parent:
                parent = Category.search([('name', '=', 'Shopify'), ('parent_id', '=', False)], limit=1) \
                    or Category.create({'name': 'Shopify'})
                self.shopify_categ_parent_id = parent
            categ = Category.create({'name': product_type, 'parent_id': parent.id})
        template.with_context(ms_no_export=True).categ_id = categ

    def _category_collection(self, categ):
        """Manual Shopify collection of an Odoo category (found by title or created)."""
        if not categ:
            return False
        Collection = self.env['mirak.shopify.collection']
        record = Collection.search([('store_id', '=', self.id), ('categ_id', '=', categ.id)], limit=1)
        if record:
            return record.gid
        client = self._client()
        title = categ.name
        escaped = title.replace("'", "\\'")
        found = client.query(COLLECTION_FIND_QUERY, {'query': f"title:'{escaped}'"}).get('collections') or {}
        gid = next((c['id'] for c in found.get('nodes', [])
                    if c['title'].strip().lower() == title.strip().lower() and not c.get('ruleSet')), None)
        if not gid:
            gid = client.mutate(COLLECTION_CREATE_MUTATION, {'collection': {'title': title}},
                                'collectionCreate')['collection']['id']
            self._log('info', 'product_out', f"Collection created in Shopify: {title}", reference=gid)
        Collection.create({'store_id': self.id, 'categ_id': categ.id, 'gid': gid, 'name': title})
        return gid

    def _catalog_product_input(self, template, maps, dirty, new):
        """Tags / product type / collections to add to a productSet or productUpdate input."""
        data = {}
        product_gid = maps[:1].shopify_product_gid if maps else None
        if self.sync_tags and (new or dirty & {'tags'} or (dirty & {'all'} and template.ms_tags_synced)):
            data['tags'] = self._tags_for_push(template, product_gid)
        if self.category_sync != 'none' and (new or dirty & {'category', 'all'}):
            data['productType'] = self._shopify_product_type(template)
        if self.category_collections and (new or dirty & {'category', 'all'}):
            gid = self._category_collection(template.categ_id)
            current = maps[:1].collection_gid if maps else False
            if new:
                data['collections'] = [gid] if gid else []
            elif gid != current:
                if gid:
                    data['collectionsToJoin'] = [gid]
                if current:
                    data['collectionsToLeave'] = [current]
            data['_collection'] = gid  # stored on the links once sent
        metafields = self._metafields_input(template, dirty, new)
        if metafields:
            data['metafields'] = metafields
        return data

    # ------------------------------------------------------------------
    # Customers
    # ------------------------------------------------------------------

    def _pull_customers(self):
        client = self._client()
        started = fields.Datetime.now()
        search = None
        if self.last_customer_pull:
            since = self.last_customer_pull - timedelta(minutes=2)
            search = f"updated_at:>'{since.strftime(SHOPIFY_DT_FORMAT)}'"
        done = 0
        first_failure = None
        for node in client.paginate(CUSTOMERS_QUERY, {'query': search}, 'customers'):
            try:
                with self.env.cr.savepoint():
                    if self._import_customer(node):
                        done += 1
            except Exception as e:
                _logger.exception("Shopify customer %s import failed", node.get('id'))
                self._log('error', 'customer_in', f"Customer {node.get('id')}: {e}", reference=node.get('id'))
                failed_at = self._parse_shopify_dt(node['updatedAt'])
                first_failure = min(first_failure or failed_at, failed_at)
        self.last_customer_pull = min(started, first_failure - timedelta(seconds=1)) if first_failure else started
        if done:
            self._log('info', 'customer_in', f"{done} customer(s) created or updated from Shopify")

    @staticmethod
    def _customer_contact(node):
        email = ((node.get('defaultEmailAddress') or {}).get('emailAddress') or node.get('email') or '').strip().lower()
        phone = ((node.get('defaultPhoneNumber') or {}).get('phoneNumber') or node.get('phone') or '').strip()
        return email, phone

    def _find_partner(self, email, phone):
        Partner = self.env['res.partner']
        partner = Partner
        if email:
            partner = Partner.search([('email', '=ilike', email), ('parent_id', '=', False)], limit=1)
        if not partner and phone:
            partner = Partner.search([('phone', '=', phone), ('parent_id', '=', False)], limit=1)
        return partner

    def _import_customer(self, node):
        """Create or update the Odoo contact of a Shopify customer. Returns True if something changed."""
        Link = self.env['mirak.shopify.customer']
        link = Link.search([('store_id', '=', self.id), ('customer_gid', '=', node['id'])], limit=1)
        if link and link.shopify_updated_at and link.shopify_updated_at >= node['updatedAt']:
            return False
        email, phone = self._customer_contact(node)
        name = f"{node.get('firstName') or ''} {node.get('lastName') or ''}".strip()
        address = node.get('defaultAddress') or {}
        partner = link.partner_id or self._find_partner(email, phone)
        vals = dict(self._address_vals(address)) if address.get('address1') or address.get('city') else {}
        if name:
            vals['name'] = name
        if email:
            vals['email'] = email
        if phone:
            vals['phone'] = phone
        if not partner:
            if not (name or email or phone):
                return False  # nothing to identify the contact
            vals.setdefault('name', email or phone)
            partner = self.env['res.partner'].create(dict(vals, company_id=False, customer_rank=1))
        else:
            changes = {k: v for k, v in vals.items() if v and (partner[k].id if hasattr(partner[k], 'id') else partner[k]) != v}
            if changes:
                partner.write(changes)
        if link:
            link.shopify_updated_at = node['updatedAt']
        else:
            Link.create({'store_id': self.id, 'partner_id': partner.id, 'customer_gid': node['id'],
                         'shopify_updated_at': node['updatedAt']})
        return True

    def _partner_for_customer(self, customer_gid):
        if not customer_gid:
            return self.env['res.partner']
        return self.env['mirak.shopify.customer'].search(
            [('store_id', '=', self.id), ('customer_gid', '=', customer_gid)], limit=1).partner_id

    def _link_customer(self, partner, customer_gid):
        Link = self.env['mirak.shopify.customer']
        if not customer_gid or not partner or Link.search_count(
                [('store_id', '=', self.id), ('customer_gid', '=', customer_gid)]):
            return
        Link.create({'store_id': self.id, 'partner_id': partner.id, 'customer_gid': customer_gid})

    def _export_customer(self, partner):
        """Create or update the Shopify customer of an Odoo contact (matched by link, then email/phone)."""
        Link = self.env['mirak.shopify.customer']
        link = Link.search([('store_id', '=', self.id), ('partner_id', '=', partner.id)], limit=1)
        email = (partner.email or '').strip().lower()
        phone = (partner.phone or '').strip().replace(' ', '')
        if not (link or email or phone):
            raise UserError(self.env._("%s has no email or phone: Shopify needs one of them.", partner.display_name))
        names = (partner.name or '').strip().split(' ', 1)
        data = {'firstName': names[0], 'lastName': names[1] if len(names) > 1 else ''}
        if email:
            data['email'] = email
        if phone.startswith('+'):
            data['phone'] = phone
        if partner.street or partner.city:
            address = {
                'address1': partner.street or '', 'address2': partner.street2 or '', 'city': partner.city or '',
                'zip': partner.zip or '', 'company': partner.parent_id.name or (partner.name if partner.is_company else ''),
            }
            if partner.country_id:
                address['countryCode'] = partner.country_id.code
            if partner.state_id and partner.state_id.code:
                address['provinceCode'] = partner.state_id.code
            if phone.startswith('+'):
                address['phone'] = phone
            data['addresses'] = [address]
        if link:
            identifier = {'id': link.customer_gid}
        elif email:
            identifier = {'email': email}
        else:
            identifier = {'phone': phone}
        customer = self._client().mutate(CUSTOMER_SET_MUTATION, {'input': data, 'identifier': identifier},
                                         'customerSet')['customer']
        if link:
            link.shopify_updated_at = customer['updatedAt']
        else:
            Link.create({'store_id': self.id, 'partner_id': partner.id, 'customer_gid': customer['id'],
                         'shopify_updated_at': customer['updatedAt']})
        self._log('info', 'customer_out', f"Customer sent to Shopify: {partner.display_name}", reference=customer['id'])
        return customer['id']

    # ------------------------------------------------------------------
    # Orders cancelled in Odoo
    # ------------------------------------------------------------------

    def _push_cancellations(self):
        retry_before = fields.Datetime.now() - timedelta(hours=1)
        orders = self.env['sale.order'].search([
            ('ms_store_id', '=', self.id), ('ms_cancel_pending', '=', True), ('state', '=', 'cancel'),
            '|', ('ms_cancel_try', '=', False), ('ms_cancel_try', '<', retry_before),
        ])
        for order in orders:
            if order.picking_ids.filtered(lambda p: p.state == 'done' and p.picking_type_code == 'outgoing'):
                order.ms_cancel_pending = False
                self._log('warning', 'order_out', f"{order.name} ({order.ms_order_name}) was delivered: not cancelled "
                                                  f"in Shopify. Refund it in Shopify if needed.",
                          reference=order.ms_order_gid)
                continue
            try:
                self._client().mutate(ORDER_CANCEL_MUTATION, {
                    'orderId': order.ms_order_gid,
                    'restock': self.cancel_restock,
                    'notify': self.cancel_notify,
                    'refund': {'originalPaymentMethodsRefund': self.cancel_refund},
                    'note': f"Cancelled in Odoo ({order.name})",
                }, 'orderCancel')
            except ShopifyError as e:
                order.ms_cancel_try = fields.Datetime.now()
                self._log('error', 'order_out', f"{order.name} ({order.ms_order_name}): not cancelled in Shopify: {e}",
                          reference=order.ms_order_gid)
                continue
            order.write({'ms_cancel_pending': False, 'ms_cancel_try': False})
            if self.cancel_restock:
                # Shopify puts the quantities back in 'available': expect them in the stock step
                self._shift_expected_stock(order, +1)
            self._log('info', 'order_out', f"{order.name}: {order.ms_order_name} cancelled in Shopify.",
                      reference=order.ms_order_gid)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    ms_customer_ids = fields.One2many('mirak.shopify.customer', 'partner_id', string='Shopify Customers')

    def action_ms_send_to_shopify(self):
        stores = self.env['mirak.shopify.store'].search([('state', '=', 'connected')])
        if not stores:
            raise UserError(self.env._("No connected Shopify store."))
        sent, errors = 0, []
        for partner in self:
            for store in stores:
                try:
                    with self.env.cr.savepoint():
                        store._export_customer(partner)
                    sent += 1
                except Exception as e:
                    errors.append(f"{partner.display_name} ({store.name}): {e}")
        message = self.env._("%s customer(s) sent to Shopify.", sent)
        if errors:
            message += "\n" + "\n".join(errors[:5])
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'title': self.env._("Shopify"), 'message': message,
                       'type': 'warning' if errors else 'success', 'sticky': bool(errors)},
        }
