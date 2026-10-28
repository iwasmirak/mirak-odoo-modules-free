# Copyright 2026 Bouchibane Karim (Mirak)
# License OPL-1 (https://www.odoo.com/documentation/19.0/legal/licenses.html)
"""Run the official PINT-AE 1.0.4 Schematron rules (XSLT 2.0, shipped in static/schematron).

XSLT 2.0 needs Saxon: the rules run only when the optional ``saxonche`` package is installed
(``pip install saxonche``). Without it the module still checks the document with its own Python rules and the
UBL 2.1 schema, and says so on the pre-validation result.
"""
import os
import threading

from lxml import etree

try:
    from saxonche import PySaxonProcessor
except ImportError:     # optional dependency
    PySaxonProcessor = None

BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'static', 'schematron', 'pint-ae-1.0.4')
STYLESHEETS = ('PINT-UBL-validation-preprocessed.xslt', 'PINT-jurisdiction-aligned-rules.xslt')
FOLDERS = {'Invoice': 'invoice', 'CreditNote': 'creditnote'}
SVRL = '{http://purl.oclc.org/dsdl/svrl}'

_lock = threading.Lock()
_state = {'processor': None, 'executables': {}}


def available():
    return PySaxonProcessor is not None


def _executable(root_name, stylesheet):
    key = (root_name, stylesheet)
    if key not in _state['executables']:
        if _state['processor'] is None:
            _state['processor'] = PySaxonProcessor(license=False)
        compiler = _state['processor'].new_xslt30_processor()
        _state['executables'][key] = compiler.compile_stylesheet(
            stylesheet_file=os.path.join(BASE_DIR, FOLDERS[root_name], stylesheet))
    return _state['executables'][key]


def validate(xml_content):
    """bytes -> list of (rule id, flag, text). Returns None when Saxon is not installed."""
    if not available():
        return None
    root_name = etree.QName(etree.fromstring(xml_content)).localname
    if root_name not in FOLDERS:
        return [('', 'fatal', 'Unexpected root element %s' % root_name)]
    results = []
    with _lock:
        processor = _state['processor'] or PySaxonProcessor(license=False)
        _state['processor'] = processor
        source = processor.parse_xml(xml_text=xml_content.decode('utf-8'))
        for stylesheet in STYLESHEETS:
            output = _executable(root_name, stylesheet).transform_to_string(xdm_node=source)
            report = etree.fromstring(output.encode('utf-8'))
            for node in report.iter(SVRL + 'failed-assert', SVRL + 'successful-report'):
                text = ' '.join(''.join(node.itertext()).split())
                results.append((node.get('id') or '', node.get('flag') or 'fatal', text))
    return results
