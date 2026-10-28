# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Readiness of a company for UAE e-invoicing: a list of weighted checks, each with a way to fix it."""
from odoo import models

from ..tools import pint_ae

SECTIONS = [
    ('company', 'Company', 'الشركة'),
    ('provider', 'Service Provider', 'مزوّد الخدمة'),
    ('taxes', 'Taxes', 'الضرائب'),
    ('customers', 'Customers', 'العملاء'),
    ('products', 'Products', 'المنتجات'),
    ('documents', 'Documents', 'المستندات'),
]


class ResCompany(models.Model):
    _inherit = 'res.company'

    def _mirak_ae_readiness_checks(self):
        """-> list of dicts: key, section, en, ar, ok, count, weight, fix ({'type': 'records'|'settings'|...})."""
        self.ensure_one()
        env = self.env
        partner = self.partner_id
        checks = []

        def add(key, section, ok, weight, en, ar, count=0, fix=None):
            checks.append({'key': key, 'section': section, 'ok': bool(ok), 'weight': weight, 'en': en, 'ar': ar,
                           'count': count, 'fix': fix or {'type': 'settings'}})

        # -- company ------------------------------------------------------------------------------------------
        add('company_trn', 'company', pint_ae.is_valid_trn(self.mirak_ae_trn), 3,
            'Tax Registration Number (15 digits, 1…03)', 'رقم التسجيل الضريبي (15 رقمًا، يبدأ بـ 1 وينتهي بـ 03)')
        add('company_endpoint', 'company', pint_ae.is_valid_tin(self.mirak_ae_endpoint_id), 2,
            'Peppol participant ID (0235 + TIN)', 'معرّف المشارك في Peppol (0235 + TIN)')
        legal_ok = bool(self.mirak_ae_legal_id and self.mirak_ae_legal_id_type and
                        (self.mirak_ae_legal_id_type not in ('TL', 'PAS') or self.mirak_ae_legal_authority))
        add('company_legal', 'company', legal_ok, 2,
            'Trade licence number, type and issuing authority', 'رقم الرخصة التجارية ونوعها والجهة المُصدِرة')
        emirate = self.mirak_ae_emirate or {'AZ': 'AUH', 'DU': 'DXB', 'SH': 'SHJ', 'AJ': 'AJM', 'UQ': 'UAQ',
                                            'RK': 'RAK', 'FU': 'FUJ'}.get(partner.state_id.code)
        add('company_address', 'company', partner.street and partner.city and emirate, 2,
            'Company address: street, city and emirate', 'عنوان الشركة: الشارع والمدينة والإمارة',
            fix={'type': 'record', 'model': 'res.company', 'id': self.id})
        add('mandate_date', 'company', self.mirak_ae_mandate_date, 1,
            'Date from which e-invoicing is mandatory for you', 'تاريخ إلزامية الفوترة الإلكترونية لشركتك')
        # -- provider -----------------------------------------------------------------------------------------
        provider = self.mirak_ae_provider_id
        add('provider', 'provider', provider, 3,
            'Accredited Service Provider (ASP) selected', 'اختيار مزوّد الخدمة المعتمد (ASP)')
        add('provider_test', 'provider', provider and provider.last_test_ok, 1,
            'Connection to the provider tested successfully', 'تم اختبار الاتصال بمزوّد الخدمة بنجاح',
            fix={'type': 'record', 'model': 'mirak.ae.provider', 'id': provider.id} if provider else None)
        add('provider_production', 'provider', provider and provider.connector != 'mock' and
            provider.environment == 'production', 1,
            'Real provider in production mode (not the simulation)', 'مزوّد حقيقي في وضع الإنتاج (وليس المحاكاة)',
            fix={'type': 'records', 'model': 'mirak.ae.provider', 'domain': []})
        # -- taxes --------------------------------------------------------------------------------------------
        Tax = env['account.tax'].with_context(active_test=True)
        tax_domain = [('company_id', '=', self.id), ('type_tax_use', '=', 'sale')]
        unmapped = Tax.search(tax_domain + [('mirak_ae_category', '=', False)])
        add('taxes_category', 'taxes', not unmapped, 3,
            'Every sales tax has a PINT-AE category', 'لكل ضريبة مبيعات فئة PINT-AE', len(unmapped),
            {'type': 'records', 'model': 'account.tax', 'domain': [('id', 'in', unmapped.ids)]})
        exempt = Tax.search(tax_domain + [('mirak_ae_category', '=', 'E'), ('mirak_ae_exemption_code', '=', False)])
        add('taxes_exempt', 'taxes', not exempt, 2,
            'Exempt taxes have an exemption reason code', 'للضرائب المعفاة رمز سبب الإعفاء', len(exempt),
            {'type': 'records', 'model': 'account.tax', 'domain': [('id', 'in', exempt.ids)]})
        wrong_rate = Tax.search(tax_domain + [('mirak_ae_category', '=', 'S'), ('amount', '!=', 5.0)])
        add('taxes_rate', 'taxes', not wrong_rate, 1,
            'Standard-rated taxes are 5%', 'الضرائب القياسية بنسبة 5%', len(wrong_rate),
            {'type': 'records', 'model': 'account.tax', 'domain': [('id', 'in', wrong_rate.ids)]})
        # -- customers ----------------------------------------------------------------------------------------
        Partner = env['res.partner']
        customers = [('customer_rank', '>', 0), ('parent_id', '=', False), ('mirak_ae_scope', 'in', ('b2b', 'b2g')),
                     '|', ('company_id', '=', False), ('company_id', '=', self.id)]
        no_trn = Partner.search(customers + [('country_id.code', '=', 'AE'), ('mirak_ae_trn', '=', False)])
        add('customers_trn', 'customers', not no_trn, 2,
            'UAE business customers have a TRN', 'لعملاء الأعمال في الإمارات رقم تسجيل ضريبي', len(no_trn),
            {'type': 'records', 'model': 'res.partner', 'domain': [('id', 'in', no_trn.ids)]})
        no_address = Partner.search(customers + ['|', '|', ('street', '=', False), ('city', '=', False), '&',
                                                 ('country_id.code', '=', 'AE'), '&',
                                                 ('mirak_ae_emirate', '=', False), ('state_id', '=', False)])
        add('customers_address', 'customers', not no_address, 2,
            'Business customers have a full address (street, city, emirate)',
            'لعملاء الأعمال عنوان كامل (الشارع والمدينة والإمارة)', len(no_address),
            {'type': 'records', 'model': 'res.partner', 'domain': [('id', 'in', no_address.ids)]})
        unchecked = Partner.search(customers + [('mirak_ae_peppol_status', '!=', 'registered'),
                                                ('mirak_ae_endpoint_id', '!=', False)])
        add('customers_peppol', 'customers', not unchecked, 1,
            'Business customers confirmed on Peppol', 'تم التحقّق من تسجيل عملاء الأعمال في Peppol', len(unchecked),
            {'type': 'records', 'model': 'res.partner', 'domain': [('id', 'in', unchecked.ids)]})
        # -- products -----------------------------------------------------------------------------------------
        Product = env['product.template']
        sold = [('sale_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', self.id)]
        incomplete = Product.search(sold + ['|', '&', ('mirak_ae_item_type', 'in', ('G', 'B')),
                                            ('mirak_ae_hs_code', '=', False), '&',
                                            ('mirak_ae_item_type', 'in', ('S', 'B')),
                                            ('mirak_ae_sac_code', '=', False)])
        add('products_codes', 'products', not incomplete, 2,
            'Goods have an HS code, services a service accounting code',
            'للسلع رمز HS وللخدمات رمز محاسبة الخدمة (SAC)', len(incomplete),
            {'type': 'records', 'model': 'product.template', 'domain': [('id', 'in', incomplete.ids)]})
        untyped = Product.search(sold + [('mirak_ae_item_type', '=', False)])
        add('products_type', 'products', not untyped, 1,
            'Products sold have an item type (goods / services)', 'للمنتجات المباعة نوع (سلع / خدمات)', len(untyped),
            {'type': 'records', 'model': 'product.template', 'domain': [('id', 'in', untyped.ids)]})
        # -- documents ----------------------------------------------------------------------------------------
        Move = env['account.move']
        base = [('company_id', '=', self.id), ('move_type', 'in', ('out_invoice', 'out_refund'))]
        failed = Move.search(base + [('mirak_ae_state', 'in', ('rejected', 'error'))])
        add('documents_failed', 'documents', not failed, 2,
            'No rejected or failed e-invoice', 'لا توجد فواتير إلكترونية مرفوضة أو فاشلة', len(failed),
            {'type': 'records', 'model': 'account.move', 'domain': [('id', 'in', failed.ids)]})
        waiting = Move.search(base + [('state', '=', 'posted'), ('mirak_ae_state', 'in', ('draft', 'ready'))])
        add('documents_waiting', 'documents', not waiting, 1,
            'No posted invoice waiting to be sent', 'لا توجد فواتير مُرحّلة بانتظار الإرسال', len(waiting),
            {'type': 'records', 'model': 'account.move', 'domain': [('id', 'in', waiting.ids)]})
        return checks

    def _mirak_ae_readiness_score(self, checks=None):
        checks = checks if checks is not None else self._mirak_ae_readiness_checks()
        total = sum(check['weight'] for check in checks) or 1
        return round(100.0 * sum(check['weight'] for check in checks if check['ok']) / total)
