# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Returns in both directions.

Shopify -> Odoo: a return closed in Shopify gives back to Odoo the quantities Shopify restocked
(validated return receipt in the warehouse of the restock location); Shopify's own restock is then
expected by the stock step, so nothing is counted twice.
Odoo -> Shopify: a return receipt validated in Odoo for a Shopify order opens a return in Shopify
(for the customer service and refund); if Shopify restocks it later, that restock is expected too.
"""
import logging
from datetime import timedelta

from odoo import fields, models

from .shopify_client import ShopifyError

_logger = logging.getLogger(__name__)

ORDER_RETURNS_QUERY = """
query MsOrderReturns($id: ID!) {
  order(id: $id) {
    id
    returns(first: 20) {
      nodes {
        id
        name
        status
        reverseFulfillmentOrders(first: 10) {
          nodes {
            lineItems(first: 50) {
              nodes {
                fulfillmentLineItem { lineItem { id } }
                dispositions { id type quantity location { id } }
              }
            }
          }
        }
      }
    }
  }
}
"""

RETURNABLE_QUERY = """
query MsReturnable($id: ID!) {
  returnableFulfillments(orderId: $id, first: 10) {
    nodes {
      returnableFulfillmentLineItems(first: 50) {
        nodes { quantity fulfillmentLineItem { id lineItem { id } } }
      }
    }
  }
}
"""

RETURN_CREATE_MUTATION = """
mutation MsReturnCreate($input: ReturnInput!) {
  returnCreate(returnInput: $input) {
    return { id name status }
    userErrors { field message }
  }
}
"""

RETURN_SCOPE_HINT = "Add the access scopes read_returns and write_returns to the Shopify app."


class MirakShopifyReturn(models.Model):
    _name = 'mirak.shopify.return'
    _description = 'Shopify Return'
    _order = 'id desc'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    order_id = fields.Many2one('sale.order', ondelete='cascade', index=True)
    gid = fields.Char(string='Shopify Return ID', required=True)
    name = fields.Char()
    origin = fields.Selection([('shopify', 'Shopify'), ('odoo', 'Odoo')], required=True, default='shopify')
    picking_id = fields.Many2one('stock.picking', string='Return Receipt')
    restock_done = fields.Boolean(help="Shopify's restock of this return was taken into account")

    _sql_constraints = [('_store_gid_uniq', 'unique(store_id, gid)', 'This return is already linked.')]


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    ms_return_pending = fields.Boolean(string='Return to Send to Shopify', copy=False, readonly=True)
    ms_return_try = fields.Datetime(copy=False, readonly=True)

    def _action_done(self):
        res = super()._action_done()
        if not self.env.context.get('ms_from_shopify'):
            for picking in self.filtered(lambda p: p.picking_type_code == 'incoming' and p.return_id):
                order = picking.sale_id or picking.return_id.sale_id
                if order.ms_order_gid and order.ms_store_id.returns_to_shopify:
                    picking.write({'ms_return_pending': True, 'ms_return_try': False})
        return res


class MirakShopifyStoreReturns(models.Model):
    _inherit = 'mirak.shopify.store'

    import_returns = fields.Boolean(
        string='Shopify Returns → Odoo', default=False,
        help="When a return is closed in Shopify, the quantities restocked by Shopify come back into the "
             "Odoo warehouse of the restock location (validated return receipt).")
    returns_to_shopify = fields.Boolean(
        string='Odoo Returns → Shopify', default=False,
        help="A return receipt validated in Odoo for a Shopify order opens a return in Shopify.")

    # ------------------------------------------------------------------
    # Shopify -> Odoo
    # ------------------------------------------------------------------

    def _process_returns(self, order):
        data = self._client().query(ORDER_RETURNS_QUERY, {'id': order.ms_order_gid}).get('order') or {}
        Return = self.env['mirak.shopify.return']
        for node in (data.get('returns') or {}).get('nodes', []):
            if node['status'] != 'CLOSED':
                continue  # handled once Shopify has processed it completely
            record = Return.search([('store_id', '=', self.id), ('gid', '=', node['id'])], limit=1)
            if record and (record.restock_done or record.origin == 'shopify'):
                continue
            restocked = {}  # (line item gid, location gid) -> quantity
            for rfo in (node.get('reverseFulfillmentOrders') or {}).get('nodes', []):
                for item in (rfo.get('lineItems') or {}).get('nodes', []):
                    line_gid = ((item.get('fulfillmentLineItem') or {}).get('lineItem') or {}).get('id')
                    for disposition in item.get('dispositions') or []:
                        if disposition['type'] == 'RESTOCKED' and line_gid:
                            key = (line_gid, ((disposition.get('location') or {}).get('id')))
                            restocked[key] = restocked.get(key, 0) + disposition['quantity']
            if record:
                # Opened from Odoo (stock already back in Odoo): only expect Shopify's restock
                self._expect_restock(order, restocked)
                record.restock_done = True
                continue
            picking = self._create_odoo_return(order, node, restocked) if restocked else False
            self._expect_restock(order, restocked)
            Return.create({'store_id': self.id, 'order_id': order.id, 'gid': node['id'], 'name': node.get('name'),
                           'origin': 'shopify', 'picking_id': picking.id if picking else False, 'restock_done': True})
            self._log('info', 'order_in', f"Return {node.get('name')} of {order.ms_order_name}: "
                                          + (f"{picking.name} validated" if picking else "nothing restocked"),
                      reference=node['id'])

    def _expect_restock(self, order, restocked):
        """Shopify restocked these quantities: the stock step must not apply them a second time."""
        for (line_gid, location_gid), qty in restocked.items():
            line = order.order_line.filtered(lambda l: l.ms_line_gid == line_gid)[:1]
            if not line or not line.product_id.is_storable:
                continue
            location, _warehouse = self._location_for_gid(location_gid)
            if location_gid and location.gid != location_gid:
                continue  # restocked at a location that is not synchronized
            link = self.env['mirak.shopify.product.map'].search(
                [('store_id', '=', self.id), ('product_id', '=', line.product_id.id)], limit=1)
            if link:
                state = self._location_states(location, link)
                if state.qty_initialized:
                    state.last_qty += qty

    def _create_odoo_return(self, order, node, restocked):
        """Validated return receipt for the restocked quantities, into the restock location's warehouse."""
        quantities = {}
        warehouse = False
        for (line_gid, location_gid), qty in restocked.items():
            quantities[line_gid] = quantities.get(line_gid, 0) + qty
            warehouse = warehouse or self._location_for_gid(location_gid)[1]
        delivery = order.picking_ids.filtered(lambda p: p.picking_type_code == 'outgoing' and p.state == 'done')[:1]
        if not delivery:
            self._log('warning', 'order_in', f"Return {node.get('name')} of {order.ms_order_name}: the order was not "
                                             f"delivered in Odoo, no return receipt created.", reference=node['id'])
            return False
        wizard = self.env['stock.return.picking'].with_context(
            active_id=delivery.id, active_ids=delivery.ids, active_model='stock.picking').create({})
        for return_line in wizard.product_return_moves:
            gid = return_line.move_id.sale_line_id.ms_line_gid
            return_line.quantity = quantities.get(gid, 0)
        action = wizard.action_create_returns()
        picking = self.env['stock.picking'].browse(action['res_id'])
        if warehouse and picking.location_dest_id != warehouse.lot_stock_id:
            picking.write({'location_dest_id': warehouse.lot_stock_id.id})
            picking.move_ids.write({'location_dest_id': warehouse.lot_stock_id.id})
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty
            move.picked = True
        picking.with_context(ms_from_shopify=True, skip_backorder=True, skip_sms=True).button_validate()
        return picking

    # ------------------------------------------------------------------
    # Odoo -> Shopify
    # ------------------------------------------------------------------

    def _push_returns(self):
        retry_before = fields.Datetime.now() - timedelta(hours=1)
        pickings = self.env['stock.picking'].search([
            ('ms_return_pending', '=', True), ('state', '=', 'done'),
            '|', ('ms_return_try', '=', False), ('ms_return_try', '<', retry_before),
        ]).filtered(lambda p: (p.sale_id or p.return_id.sale_id).ms_store_id == self)
        for picking in pickings:
            order = picking.sale_id or picking.return_id.sale_id
            try:
                with self.env.cr.savepoint():
                    self._create_shopify_return(order, picking)
            except Exception as e:
                message = str(e)
                if 'access' in message.lower() or 'scope' in message.lower():
                    message = f"{message} — {RETURN_SCOPE_HINT}"
                picking.ms_return_try = fields.Datetime.now()
                self._log('error', 'order_out', f"{picking.name} ({order.ms_order_name}): {message}",
                          reference=order.ms_order_gid)

    def _create_shopify_return(self, order, picking):
        returned = {}
        for move in picking.move_ids.filtered(lambda m: m.state == 'done'):
            gid = (move.origin_returned_move_id.sale_line_id or move.sale_line_id).ms_line_gid
            if gid:
                returned[gid] = returned.get(gid, 0) + int(move.quantity)
        data = self._client().query(RETURNABLE_QUERY, {'id': order.ms_order_gid})
        items = []
        for fulfillment in (data.get('returnableFulfillments') or {}).get('nodes', []):
            for line in fulfillment['returnableFulfillmentLineItems']['nodes']:
                gid = line['fulfillmentLineItem']['lineItem']['id']
                quantity = min(returned.get(gid, 0), line['quantity'])
                if quantity > 0:
                    items.append({'fulfillmentLineItemId': line['fulfillmentLineItem']['id'], 'quantity': quantity,
                                  'returnReasonNote': f"Returned in Odoo ({picking.name})"})
                    returned[gid] -= quantity
        if not items:
            picking.ms_return_pending = False
            self._log('warning', 'order_out', f"{picking.name}: nothing returnable left in Shopify for "
                                              f"{order.ms_order_name}.", reference=order.ms_order_gid)
            return
        result = self._client().mutate(RETURN_CREATE_MUTATION, {
            'input': {'orderId': order.ms_order_gid, 'returnLineItems': items}}, 'returnCreate')['return']
        self.env['mirak.shopify.return'].create({
            'store_id': self.id, 'order_id': order.id, 'gid': result['id'], 'name': result.get('name'),
            'origin': 'odoo', 'picking_id': picking.id})
        picking.write({'ms_return_pending': False, 'ms_return_try': False})
        self._log('info', 'order_out', f"{picking.name}: return {result.get('name')} opened in Shopify for "
                                       f"{order.ms_order_name}.", reference=result['id'])
