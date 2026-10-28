# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Structural validation against the official OASIS UBL 2.1 schemas shipped in static/schemas/ubl-2.1
(xsdrt "runtime" flavour of https://docs.oasis-open.org/ubl/os-UBL-2.1/UBL-2.1.zip)."""
import os
import threading

from lxml import etree

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'schemas', 'ubl-2.1', 'maindoc')
SCHEMA_FILES = {'Invoice': 'UBL-Invoice-2.1.xsd', 'CreditNote': 'UBL-CreditNote-2.1.xsd'}
_cache = {}
_lock = threading.Lock()


def _schema(root_name):
    with _lock:
        if root_name not in _cache:
            _cache[root_name] = etree.XMLSchema(etree.parse(os.path.join(SCHEMA_DIR, SCHEMA_FILES[root_name])))
        return _cache[root_name]


def validate_xsd(xml_content):
    """bytes -> list of error strings (empty when the document is valid UBL 2.1)."""
    try:
        doc = etree.fromstring(xml_content)
    except etree.XMLSyntaxError as error:
        return ['XML syntax: %s' % error]
    root_name = etree.QName(doc).localname
    if root_name not in SCHEMA_FILES:
        return ['Unexpected root element %s' % root_name]
    schema = _schema(root_name)
    if schema.validate(doc):
        return []
    return ['line %s: %s' % (error.line, error.message) for error in schema.error_log]
