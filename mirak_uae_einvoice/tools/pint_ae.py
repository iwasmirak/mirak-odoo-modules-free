# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""PINT-AE constants and identifier helpers.

Every value below comes from the official Peppol PINT-AE 1.0.4 artefacts (docs.peppol.eu/poac/ae, examples,
code lists and Schematron) or from the UAE Ministry of Finance "Electronic Invoice Mandatory Fields" V1.0
(23 February 2026). Nothing here is guessed; open points are listed in the README.
"""
import re

# IBT-024 / IBT-023 (all 30 official examples)
CUSTOMIZATION_ID = 'urn:peppol:pint:billing-1@ae-1'
PROFILE_ID = 'urn:peppol:bis:billing'

# Electronic address scheme for UAE participants (EAS code list, MoF: "fixed value 0235")
UAE_EAS = '0235'
# Predefined FTA endpoints found in the official examples
ENDPOINT_BUYER_NOT_ONBOARDED = '9900000098'
ENDPOINT_EXPORTS = '9900000099'

# Country subdivisions allowed when the country is AE (rule ibr-128-ae)
EMIRATES = [
    ('AUH', 'Abu Dhabi'),
    ('DXB', 'Dubai'),
    ('SHJ', 'Sharjah'),
    ('AJM', 'Ajman'),
    ('UAQ', 'Umm Al Quwain'),
    ('RAK', 'Ras Al Khaimah'),
    ('FUJ', 'Fujairah'),
]

# Legal registration identifier types (MoF mandatory fields, BTAE-15/16)
LEGAL_ID_TYPES = [
    ('TL', 'Commercial / Trade license'),
    ('EID', 'Emirates ID'),
    ('PAS', 'Passport'),
    ('CD', 'Cabinet decision'),
]

# Tax category codes (Aligned-TaxCategoryCodes.gc)
TAX_CATEGORIES = [
    ('S', 'S - Standard rate (5%)'),
    ('Z', 'Z - Zero rated'),
    ('E', 'E - Exempt'),
    ('O', 'O - Outside the scope of VAT'),
    ('AE', 'AE - Reverse charge'),
]

# Tax exemption reason codes (Aligned-TaxExemptionCodes.gc)
EXEMPTION_CODES = [
    ('DL8.46.1', 'Certain financial services'),
    ('DL8.46.2', 'Supply of residential units (lease or sale)'),
    ('DL8.46.3', 'Bare land'),
    ('DL8.46.4', 'Local passenger transport'),
]

# Invoice transaction type flags (transactiontype.gc): position -> code
TRANSACTION_FLAGS = [
    ('free_trade_zone', 'Free trade zone'),
    ('deemed_supply', 'Deemed supply'),
    ('margin_scheme', 'Profit margin scheme'),
    ('summary_invoice', 'Summary invoice'),
    ('continuous_supply', 'Continuous supply'),
    ('agent_billing', 'Disclosed agent billing'),
    ('ecommerce', 'Supply through e-commerce'),
    ('exports', 'Exports'),
]

# Item type (ItemType.gc), carried in cac:CommodityClassification/cbc:CommodityCode
ITEM_TYPES = [('G', 'Goods'), ('S', 'Services'), ('B', 'Both')]

# Reasons for credit note (CreditReason.gc), BTAE-03 in cac:DiscrepancyResponse/cbc:ResponseCode
CREDIT_REASONS = [
    ('DL8.61.1.A', 'The supply was cancelled'),
    ('DL8.61.1.B', 'The tax treatment changed because the nature of the supply changed'),
    ('DL8.61.1.C', 'The agreed consideration was altered (e.g. bad debt relief)'),
    ('DL8.61.1.D', 'Goods or services returned, in full or in part'),
    ('DL8.61.1.E', 'Tax charged or tax treatment applied in error'),
    ('VD', 'Volume discount'),
]

# Type of goods under the reverse charge mechanism (GoodsType.gc), BTAE-09 in cbc:NatureCode
GOODS_TYPES = [
    ('DL8.48.3.1', 'Crude or refined oil'),
    ('DL8.48.3.2', 'Unprocessed or processed natural gas'),
    ('DL8.48.3.3', 'Pure hydrocarbons'),
    ('DL8.48.8.1', 'Gold and diamonds'),
    ('DL8.48.8.2', 'Electronic devices'),
]

# Payment means (UNCL4461.gc, the values allowed by PINT-AE)
PAYMENT_MEANS = [
    ('1', 'Instrument not defined'),
    ('10', 'In cash'),
    ('20', 'Cheque'),
    ('21', "Banker's draft"),
    ('30', 'Credit transfer'),
    ('49', 'Direct debit'),
    ('54', 'Credit card'),
    ('55', 'Debit card'),
    ('68', 'Online payment service'),
]

# Frequency of billing (FreqBilling.gc), BTAE-06 in cac:InvoicePeriod/cbc:DescriptionCode
BILLING_FREQUENCIES = [
    ('DLY', 'Daily'), ('WKY', 'Weekly'), ('Q15', 'Every 15 days'), ('MTH', 'Monthly'),
    ('Q45', 'Every 45 days'), ('Q60', 'Every 60 days'), ('QTR', 'Quarterly'), ('YRL', 'Yearly'),
    ('HYR', 'Half-yearly'), ('OTH', 'Others'),
]

TRN_LENGTH = 15
TIN_LENGTH = 10
# rule ibr-132-ae: "must be 15 digits, starting with 1, ending with 03"
TRN_PATTERN = r'1\d{12}03'
# rule ibr-148-ae: the TIN "must be 10 numeric digits ... of the format 1XXXXXXXXX"
TIN_PATTERN = r'1\d{9}'


def normalize_trn(value):
    """'100-1234-5678-9003 ' -> '100123456789003' (digits only); '' when empty."""
    return re.sub(r'\D', '', value or '')


def is_valid_trn(value):
    """15 digits, starting with 1 and ending with 03 (PINT-AE rule ibr-132-ae)."""
    return bool(re.fullmatch(TRN_PATTERN, normalize_trn(value)))


def is_valid_tin(value):
    """10 digits starting with 1 (PINT-AE rule ibr-148-ae)."""
    return bool(re.fullmatch(TIN_PATTERN, value or ''))


def tin_from_trn(value):
    """TIN = the first 10 digits of the 15-digit TRN (MoF mandatory fields, glossary)."""
    trn = normalize_trn(value)
    return trn[:TIN_LENGTH] if len(trn) == TRN_LENGTH else ''


def is_valid_endpoint(scheme, identifier):
    """UAE endpoints are 0235 + a 10-digit TIN (or a predefined 99000000xx endpoint)."""
    if scheme != UAE_EAS:
        return bool(identifier)
    return bool(re.fullmatch(r'\d{%d}' % TIN_LENGTH, identifier or ''))


def transaction_type_code(flags):
    """{'exports': True, ...} -> '00000001' (ProfileExecutionID, BTAE-02)."""
    return ''.join('1' if flags.get(key) else '0' for key, _label in TRANSACTION_FLAGS)
