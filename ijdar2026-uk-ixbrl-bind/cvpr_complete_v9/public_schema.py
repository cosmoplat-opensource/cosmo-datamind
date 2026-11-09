"""Version-exact factual schema declarations, shared by every diagnostic control.

This does not infer a display sign from an XBRL debit/credit declaration.
Partial downloads fail XML parsing and are never treated as valid catalogs.
"""
import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

from prepare import OUT, save, sha

XS = '{http://www.w3.org/2001/XMLSchema}'
XBRLI = '{http://www.xbrl.org/2003/instance}'
URL = 'https://xbrl.ifrs.org/taxonomy/2022-03-24/full_ifrs/full_ifrs-cor_2022-03-24.xsd'


def declarations(path):
    root = ET.parse(path).getroot()
    namespace = root.get('targetNamespace')
    if root.tag != XS + 'schema' or not namespace:
        raise ValueError('Not an XML schema with a declared namespace')
    fields = {}
    for element in root.findall(XS + 'element'):
        name = element.get('name')
        if not name:
            continue
        if name in fields:
            raise ValueError('Duplicate field declaration')
        fields[name] = {'name': name, 'namespace': namespace,
                        'type': element.get('type'),
                        'balance': element.get(XBRLI + 'balance'),
                        'period_type': element.get(XBRLI + 'periodType'),
                        'abstract': element.get('abstract') == 'true'}
    if not fields:
        raise ValueError('Empty field catalog')
    return {'namespace': namespace, 'fields': fields}


def lookup(catalog, namespace, name):
    if namespace != catalog['namespace']:
        return {'name': name, 'namespace': namespace, 'type': None,
                'balance': None, 'period_type': None, 'abstract': None,
                'declaration_status': 'unknown_namespace_or_version'}
    result = catalog['fields'].get(name)
    if result is None:
        return {'name': name, 'namespace': namespace, 'type': None,
                'balance': None, 'period_type': None, 'abstract': None,
                'declaration_status': 'field_not_in_exact_schema'}
    return dict(result, declaration_status='exact_public_schema')


def main(path):
    catalog = declarations(path)
    if catalog['namespace'] != 'https://xbrl.ifrs.org/taxonomy/2022-03-24/ifrs-full':
        raise ValueError('Downloaded taxonomy version differs')
    dest = OUT / 'expanded_development_sources/public_schema'
    save(dest / 'ifrs_20220324_properties.json', catalog)
    save(dest / 'provenance.json', {
        'schema_source': URL,
        'release_page': 'https://www.ifrs.org/issued-standards/ifrs-taxonomy/ifrs-accounting-taxonomy-2022/',
        'source_file': str(Path(path).resolve()), 'source_sha256': sha(path),
        'source_xml_complete_and_parsed': True, 'fields': len(catalog['fields']),
        'catalog_sha256': sha(dest / 'ifrs_20220324_properties.json'),
        'factual_declaration_attributes_only': True,
        'display_polarity_not_determined_by_balance_alone': True,
        'other_taxonomy_versions_remain_unknown': True,
        'all_methods_receive_the_same_declarations': True,
        'source_fact_values_or_locations_included': False,
        'code_sha256': sha(__file__)})
    print('Complete public field catalog:', len(catalog['fields']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('schema', type=Path)
    main(parser.parse_args().schema)
