# Copyright 2026 Bouchibane Karim
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Shopify Markets prices (one Odoo pricelist per Shopify price list), metafields mapped to Odoo
product fields, and translations of product names and descriptions."""
import logging

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

PRICE_LISTS_QUERY = """
query MsPriceLists { priceLists(first: 50) { nodes { id name currency catalog { title } } } }
"""

PRICE_LIST_ADD_MUTATION = """
mutation MsPriceListAdd($priceListId: ID!, $prices: [PriceListPriceInput!]!) {
  priceListFixedPricesAdd(priceListId: $priceListId, prices: $prices) {
    prices { variant { id } }
    userErrors { field message code }
  }
}
"""

SHOP_LOCALES_QUERY = """
query MsShopLocales { shopLocales(published: true) { locale primary } }
"""

TRANSLATABLE_QUERY = """
query MsTranslatable($id: ID!) {
  translatableResource(resourceId: $id) { resourceId translatableContent { key digest locale } }
}
"""

TRANSLATIONS_MUTATION = """
mutation MsTranslations($id: ID!, $translations: [TranslationInput!]!) {
  translationsRegister(resourceId: $id, translations: $translations) {
    translations { key locale }
    userErrors { field message }
  }
}
"""

METAFIELD_TYPES = [
    ('single_line_text_field', 'Text (single line)'),
    ('multi_line_text_field', 'Text (multi-line)'),
    ('number_integer', 'Integer'),
    ('number_decimal', 'Decimal'),
    ('boolean', 'True / False'),
]
FIELD_TYPES = ('char', 'text', 'html', 'integer', 'float', 'monetary', 'boolean', 'selection')


class MirakShopifyPriceList(models.Model):
    """A Shopify Markets price list and the Odoo pricelist giving its prices."""
    _name = 'mirak.shopify.price.list'
    _description = 'Shopify Market Price List'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    gid = fields.Char(string='Shopify Price List ID', required=True)
    name = fields.Char(readonly=True)
    catalog = fields.Char(readonly=True)
    currency = fields.Char(readonly=True)
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Odoo Pricelist',
        help="Prices of this pricelist (converted to the price list currency) become the fixed prices of the "
             "Shopify price list. Empty: Shopify keeps its own rules for this market.")

    _store_gid_uniq = models.Constraint('unique(store_id, gid)', 'This price list is already listed.')


class MirakShopifyMetafield(models.Model):
    """An Odoo product field kept in a Shopify product metafield."""
    _name = 'mirak.shopify.metafield'
    _description = 'Shopify Metafield Mapping'

    store_id = fields.Many2one('mirak.shopify.store', required=True, ondelete='cascade', index=True)
    field_id = fields.Many2one(
        'ir.model.fields', string='Odoo Field', required=True, ondelete='cascade',
        domain=[('model', '=', 'product.template'), ('ttype', 'in', FIELD_TYPES), ('store', '=', True)])
    namespace = fields.Char(required=True, default='custom')
    key = fields.Char(required=True)
    metafield_type = fields.Selection(METAFIELD_TYPES, string='Shopify Type', required=True,
                                      default='single_line_text_field')
    direction = fields.Selection([
        ('both', 'Both directions'),
        ('odoo', 'Odoo → Shopify'),
        ('shopify', 'Shopify → Odoo'),
    ], required=True, default='both')

    @api.constrains('field_id')
    def _check_field(self):
        for mapping in self:
            if mapping.field_id.model != 'product.template' or mapping.field_id.ttype not in FIELD_TYPES:
                raise ValidationError(self.env._("Choose a product field of type text, number, true/false or selection."))

    def _to_shopify(self, template):
        value = template[self.field_id.name]
        if self.field_id.ttype == 'boolean':
            return 'true' if value else 'false'
        if value in (False, None, ''):
            return None  # Shopify does not store empty metafields
        if self.metafield_type == 'number_integer':
            return str(int(value))
        if self.metafield_type == 'number_decimal':
            return str(float(value))
        return str(value)

    def _to_odoo(self, value):
        ttype = self.field_id.ttype
        if ttype == 'boolean':
            return str(value).lower() == 'true'
        if value in (None, ''):
            return False
        if ttype == 'integer':
            return int(float(value))
        if ttype in ('float', 'monetary'):
            return float(value)
        return value


class MirakShopifyStoreMarkets(models.Model):
    _inherit = 'mirak.shopify.store'

    price_list_ids = fields.One2many('mirak.shopify.price.list', 'store_id', string='Market Price Lists')
    metafield_ids = fields.One2many('mirak.shopify.metafield', 'store_id', string='Metafields')
    sync_translations = fields.Boolean(
        string='Translations', default=False,
        help="Send the product names and descriptions in the other Odoo languages to the Shopify languages "
             "with the same code (Odoo → Shopify).")

    # ------------------------------------------------------------------
    # Market prices
    # ------------------------------------------------------------------

    def action_fetch_price_lists(self):
        self.ensure_one()
        PriceList = self.env['mirak.shopify.price.list']
        for node in self._client().query(PRICE_LISTS_QUERY)['priceLists']['nodes']:
            vals = {'name': node['name'], 'currency': node['currency'],
                    'catalog': (node.get('catalog') or {}).get('title') or False}
            record = PriceList.search([('store_id', '=', self.id), ('gid', '=', node['id'])], limit=1)
            if record:
                record.write(vals)
            else:
                PriceList.create(dict(vals, store_id=self.id, gid=node['id']))
        return self._notify(self.env._("%s Shopify price list(s).", len(self.price_list_ids)))

    def _push_market_prices(self, template, maps):
        """Fixed prices of the mapped Shopify price lists for the variants of a product."""
        price_lists = self.price_list_ids.filtered('pricelist_id')
        if not price_lists or not maps:
            return
        client = self._client()
        Currency = self.env['res.currency'].with_context(active_test=False)
        for price_list in price_lists:
            target = Currency.search([('name', '=', price_list.currency)], limit=1)
            if not target:
                continue
            pricelist = price_list.pricelist_id
            prices = []
            for link in maps:
                price = pricelist._get_product_price(link.product_id, 1.0)
                price = self._convert_price(price, pricelist.currency_id, target)
                prices.append({'variantId': link.shopify_variant_gid,
                               'price': {'amount': self._money(price), 'currencyCode': price_list.currency}})
            client.mutate(PRICE_LIST_ADD_MUTATION, {'priceListId': price_list.gid, 'prices': prices},
                          'priceListFixedPricesAdd')

    # ------------------------------------------------------------------
    # Metafields
    # ------------------------------------------------------------------

    def _metafields_input(self, template, dirty, new):
        mappings = self.metafield_ids.filtered(lambda m: m.direction in ('both', 'odoo'))
        if not mappings or not (new or dirty & {'metafields', 'all'}):
            return []
        result = []
        for mapping in mappings:
            value = mapping._to_shopify(template)
            if value is not None:
                result.append({'namespace': mapping.namespace, 'key': mapping.key,
                               'type': mapping.metafield_type, 'value': value})
        return result

    def _apply_shopify_metafields(self, template, node):
        mappings = self.metafield_ids.filtered(lambda m: m.direction in ('both', 'shopify'))
        if not mappings or 'metafields' not in node:
            return
        values = {(m['namespace'], m['key']): m['value'] for m in (node['metafields'] or {}).get('nodes', [])}
        vals = {}
        for mapping in mappings:
            if (mapping.namespace, mapping.key) in values:
                value = mapping._to_odoo(values[(mapping.namespace, mapping.key)])
                if template[mapping.field_id.name] != value:
                    vals[mapping.field_id.name] = value
        if vals:
            template.with_context(ms_no_export=True).write(vals)

    def _products_query(self, query):
        """Product queries ask for the metafields only when some are mapped (Shopify → Odoo)."""
        if not self.metafield_ids.filtered(lambda m: m.direction in ('both', 'shopify')):
            return query
        return query.replace("  productType\n", "  productType\n  metafields(first: 50) { nodes { namespace key value } }\n", 1)

    # ------------------------------------------------------------------
    # Translations
    # ------------------------------------------------------------------

    def _shop_languages(self):
        """{Shopify locale: Odoo language code} for the published Shopify languages Odoo has."""
        locales = self._client().query(SHOP_LOCALES_QUERY)['shopLocales']
        langs = self.env['res.lang'].search([]).mapped('code')
        result = {}
        for locale in locales:
            if locale['primary']:
                continue
            code = locale['locale']
            match = next((l for l in langs if l.replace('_', '-').lower() == code.lower()), None) \
                or next((l for l in langs if l.split('_')[0].lower() == code.split('-')[0].lower()), None)
            if match:
                result[code] = match
        return result

    def _push_translations(self, template, product_gid):
        languages = self._shop_languages()
        if not languages:
            return
        client = self._client()
        resource = client.query(TRANSLATABLE_QUERY, {'id': product_gid}).get('translatableResource') or {}
        digests = {c['key']: c['digest'] for c in resource.get('translatableContent') or []}
        translations = []
        for locale, lang in languages.items():
            localized = template.with_context(lang=lang)
            for key, value in (('title', localized.name), ('body_html', self._text_to_html(localized.description_sale))):
                if value and digests.get(key):
                    translations.append({'locale': locale, 'key': key, 'value': value,
                                         'translatableContentDigest': digests[key]})
        if translations:
            client.mutate(TRANSLATIONS_MUTATION, {'id': product_gid, 'translations': translations},
                          'translationsRegister')

    def _push_product_extras(self, template, maps, dirty, new):
        """Market prices and translations, after the product itself was sent."""
        if not maps:
            return
        if self.sync_price and (new or dirty & {'price', 'all'}):
            self._push_market_prices(template, maps)
        if self.sync_translations and (new or dirty & {'name', 'description', 'all'}):
            self._push_translations(template, maps[0].shopify_product_gid)


class ProductTemplateMetafields(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get('ms_no_export'):
            mapped = set(self.env['mirak.shopify.metafield'].sudo().search(
                [('direction', 'in', ('both', 'odoo'))]).field_id.mapped('name'))
            if mapped & set(vals):
                self._ms_mark_dirty({'metafields'})
        return res
