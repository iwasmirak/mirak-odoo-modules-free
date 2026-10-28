# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Shopify Payments payouts: one journal entry per payout moves the money from the card payments
account to the bank, books the fees, and reconciles the payments of the orders it contains."""
import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

PAYOUTS_QUERY = """
query MsPayouts {
  shopifyPaymentsAccount {
    payouts(first: 30, sortKey: ISSUED_AT, reverse: true) {
      nodes {
        id
        legacyResourceId
        issuedAt
        status
        net { amount currencyCode }
      }
    }
  }
}
"""

BALANCE_TRANSACTIONS_QUERY = """
query MsBalanceTransactions($cursor: String, $query: String) {
  shopifyPaymentsAccount {
    balanceTransactions(first: 100, after: $cursor, query: $query) {
      pageInfo { hasNextPage endCursor }
      nodes {
        id
        type
        test
        amount { amount }
        fee { amount }
        net { amount }
        associatedOrder { id name }
        associatedPayout { id }
      }
    }
  }
}
"""

PAYOUT_SCOPE_HINT = "Add the access scope read_shopify_payments_payouts to the Shopify app."


class MirakShopifyPayout(models.Model):
    _name = 'mirak.shopify.payout'
    _description = 'Shopify Payments Payout'
    _order = 'issued_at desc, id desc'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    gid = fields.Char(string='Shopify Payout ID', required=True)
    name = fields.Char(compute='_compute_name')
    issued_at = fields.Datetime(string='Date')
    currency_id = fields.Many2one('res.currency')
    amount_net = fields.Monetary(string='Paid to Bank', currency_field='currency_id')
    amount_gross = fields.Monetary(string='Gross', currency_field='currency_id')
    amount_fees = fields.Monetary(string='Fees', currency_field='currency_id')
    transaction_count = fields.Integer(string='Transactions')
    move_id = fields.Many2one('account.move', string='Journal Entry', readonly=True)
    reconciled_count = fields.Integer(string='Payments Reconciled')

    _sql_constraints = [('_store_gid_uniq', 'unique(store_id, gid)', 'This payout is already imported.')]

    @api.depends('gid', 'issued_at')
    def _compute_name(self):
        for payout in self:
            payout.name = f"Payout {payout.gid.rsplit('/', 1)[-1]}" if payout.gid else ''


class MirakShopifyStoreFinance(models.Model):
    _inherit = 'mirak.shopify.store'

    import_payouts = fields.Boolean(
        string='Import Shopify Payments Payouts', default=False,
        help="Each paid payout becomes a journal entry: bank receives the net amount, fees are booked, and "
             "the card payments of its orders are reconciled.")
    payout_journal_id = fields.Many2one(
        'account.journal', string='Payout Bank Journal', check_company=True, domain="[('type', '=', 'bank')]")
    payout_fee_account_id = fields.Many2one(
        'account.account', string='Shopify Fees Account', check_company=True,
        domain="[('account_type', 'in', ('expense', 'expense_direct_cost'))]")
    payout_clearing_account_id = fields.Many2one(
        'account.account', string='Card Payments Account', check_company=True,
        domain="[('account_type', 'in', ('asset_current', 'asset_receivable'))]",
        help="Account holding the Shopify Payments card payments until they are paid out. Empty: the "
             "Outstanding Receipts account of the card payment journal.")
    payout_import_from = fields.Date(string='Payouts From', default=fields.Date.context_today,
                                     help="Older payouts are not imported.")
    payout_ids = fields.One2many('mirak.shopify.payout', 'store_id', string='Payouts')

    @api.constrains('import_payouts', 'payout_journal_id', 'payout_fee_account_id')
    def _check_payout_settings(self):
        for store in self:
            if store.import_payouts and not (store.payout_journal_id and store.payout_fee_account_id):
                raise ValidationError(self.env._("Select the payout bank journal and the Shopify fees account."))

    def _payout_clearing_account(self):
        if self.payout_clearing_account_id:
            return self.payout_clearing_account_id
        card = self.gateway_journal_ids.filtered(lambda g: g.gateway.strip().lower() == 'shopify_payments')[:1].journal_id \
            or self.payment_journal_id
        line = card.inbound_payment_method_line_ids.filtered('payment_account_id')[:1]
        return line.payment_account_id

    def _payout_bank_account(self):
        journal = self.payout_journal_id
        line = journal.inbound_payment_method_line_ids.filtered('payment_account_id')[:1]
        return line.payment_account_id or journal.default_account_id

    def _pull_payouts(self):
        client = self._client()
        Payout = self.env['mirak.shopify.payout']
        clearing = self._payout_clearing_account()
        if not clearing:
            self._log('warning', 'invoice', "Payouts not imported: set the Card Payments Account (Invoicing tab), or an "
                                            "Outstanding Receipts account on the card payment journal.")
            return
        data = client.query(PAYOUTS_QUERY).get('shopifyPaymentsAccount')
        if not data:
            return  # Shopify Payments not activated on this store
        imported = 0
        for node in data['payouts']['nodes']:
            if node['status'] != 'PAID' or Payout.search_count([('store_id', '=', self.id), ('gid', '=', node['id'])]):
                continue
            issued = self._parse_shopify_dt(node['issuedAt'].replace('.000', '')[:19] + 'Z') if node.get('issuedAt') else False
            if self.payout_import_from and issued and issued.date() < self.payout_import_from:
                continue
            try:
                with self.env.cr.savepoint():
                    self._import_payout(client, node, issued, clearing)
                imported += 1
            except Exception as e:
                _logger.exception("Shopify payout %s import failed", node['id'])
                self._log('error', 'invoice', f"Payout {node['id']}: {e}", reference=node['id'])
        if imported:
            self._log('info', 'invoice', f"{imported} Shopify payout(s) imported")

    def _payout_transactions(self, client, node):
        transactions = []
        cursor = None
        while True:
            page = client.query(BALANCE_TRANSACTIONS_QUERY, {
                'cursor': cursor, 'query': f"payments_transfer_id:{node.get('legacyResourceId')}"})['shopifyPaymentsAccount']
            conn = page['balanceTransactions']
            transactions += [t for t in conn['nodes'] if (t.get('associatedPayout') or {}).get('id') == node['id']
                             and not t.get('test')]
            if not conn['pageInfo']['hasNextPage']:
                return transactions
            cursor = conn['pageInfo']['endCursor']

    def _import_payout(self, client, node, issued, clearing):
        currency = self.env['res.currency'].with_context(active_test=False).search(
            [('name', '=', node['net']['currencyCode'])], limit=1) or self.company_id.currency_id
        transactions = self._payout_transactions(client, node)
        net = float(node['net']['amount'])
        fees = round(sum(float(t['fee']['amount']) for t in transactions), 2)
        gross = round(sum(float(t['amount']['amount']) for t in transactions), 2)
        orders = self.env['sale.order'].search([('ms_store_id', '=', self.id), ('ms_order_gid', 'in', [
            (t.get('associatedOrder') or {}).get('id') for t in transactions if t.get('associatedOrder')])])
        by_gid = {o.ms_order_gid: o for o in orders}
        label = f"Shopify payout {node['id'].rsplit('/', 1)[-1]}"

        def line(account, amount, name, partner=False):
            company_amount = currency._convert(amount, self.company_id.currency_id, self.company_id, issued.date()) \
                if currency != self.company_id.currency_id else amount
            vals = {'account_id': account.id, 'name': name, 'partner_id': partner,
                    'debit': max(company_amount, 0.0), 'credit': max(-company_amount, 0.0)}
            if currency != self.company_id.currency_id:
                vals.update(currency_id=currency.id, amount_currency=amount)
            return (0, 0, vals)

        lines = [line(self._payout_bank_account(), net, label)]
        if fees:
            lines.append(line(self.payout_fee_account_id, fees, f"{label}: Shopify fees"))
        for t in transactions:
            order = by_gid.get((t.get('associatedOrder') or {}).get('id'))
            name = f"{label}: {t['type'].lower()} {(t.get('associatedOrder') or {}).get('name') or ''}".strip()
            lines.append(line(clearing, -float(t['amount']['amount']), name, order.partner_id.id if order else False))
        rest = round(net + fees - gross, 2)
        if abs(rest) >= 0.01:
            # Not explained by the listed transactions (e.g. reserves): kept visible on the fees account
            lines.append(line(self.payout_fee_account_id, -rest, f"{label}: other adjustments"))
        move = self.env['account.move'].create({
            'move_type': 'entry', 'journal_id': self.payout_journal_id.id, 'date': issued.date(),
            'ref': label, 'line_ids': lines,
        })
        move.action_post()
        reconciled = 0
        if clearing.reconcile:
            for order in orders:
                payment_lines = self.env['account.move.line'].search([
                    ('account_id', '=', clearing.id), ('reconciled', '=', False), ('parent_state', '=', 'posted'),
                    ('move_id.origin_payment_id.memo', '=', f"Shopify {order.ms_order_name}"),
                ])
                payout_lines = move.line_ids.filtered(
                    lambda l: l.account_id == clearing and order.ms_order_name and order.ms_order_name in (l.name or ''))
                if payment_lines and payout_lines:
                    (payment_lines | payout_lines).reconcile()
                    reconciled += 1
        self.env['mirak.shopify.payout'].create({
            'store_id': self.id, 'gid': node['id'], 'issued_at': issued, 'currency_id': currency.id,
            'amount_net': net, 'amount_gross': gross, 'amount_fees': fees, 'transaction_count': len(transactions),
            'move_id': move.id, 'reconciled_count': reconciled,
        })
        self._log('info', 'invoice', f"{label}: {net:.2f} {currency.name} to the bank, fees {fees:.2f}, "
                                     f"{reconciled} order payment(s) reconciled", reference=node['id'])

    def action_view_payouts(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.env._("Shopify Payouts"),
            'res_model': 'mirak.shopify.payout',
            'view_mode': 'list,form',
            'domain': [('store_id', '=', self.id)],
        }
