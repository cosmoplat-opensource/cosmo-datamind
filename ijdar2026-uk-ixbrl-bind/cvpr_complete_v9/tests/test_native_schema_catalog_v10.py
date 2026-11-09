import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_schema_catalog_v10 import declaration_rows, english_labels, selected_package_asset


def test_package_filter_excludes_instances_reports_and_fact_files():
    assert selected_package_asset("root/taxonomy/extension.xsd")
    assert selected_package_asset("root/taxonomy/extension_lab-en.xml")
    assert not selected_package_asset("root/reports/report.xhtml")
    assert not selected_package_asset("root/reports/instance.xml")
    assert not selected_package_asset("root/facts.json")
    with pytest.raises(ValueError):
        selected_package_asset("../extension.xsd")


def test_exact_namespace_declarations_and_entity_rejection():
    body = b'''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
        xmlns:xbrli="http://www.xbrl.org/2003/instance" targetNamespace="urn:issuer:2023">
        <xs:element name="Revenue" id="issuer_Revenue" type="xbrli:monetaryItemType"
            xbrli:periodType="duration" xbrli:balance="credit"/>
        <xs:import namespace="urn:base" schemaLocation="https://example.org/base.xsd"/>
        </xs:schema>'''
    rows, imports = declaration_rows(body)
    assert rows[0]["namespace"] == "urn:issuer:2023"
    assert rows[0]["period_type"] == "duration"
    assert imports == ["https://example.org/base.xsd"]
    with pytest.raises(ValueError):
        declaration_rows(b'<!DOCTYPE schema [<!ENTITY x "invalid">]>' + body)


def test_english_label_arcs_resolve_without_reading_facts():
    body = b'''<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
    xmlns:xlink="http://www.w3.org/1999/xlink"><link:labelLink>
    <link:loc xlink:label="loc" xlink:href="issuer.xsd#issuer_Revenue"/>
    <link:label xlink:label="label" xml:lang="en">Revenue from contracts</link:label>
    <link:labelArc xlink:from="loc" xlink:to="label"/>
    </link:labelLink></link:linkbase>'''
    assert english_labels(body) == {"issuer_Revenue": ["Revenue from contracts"]}


def test_type_qname_prefix_aliases_are_not_semantic_changes():
    template = '''<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
        xmlns:{prefix}="urn:public:types" targetNamespace="urn:issuer">
        <xs:element name="A" type="{prefix}:domainItemType"/>
        </xs:schema>'''
    a = declaration_rows(template.format(prefix="dtr").encode())[0]
    b = declaration_rows(template.format(prefix="nonnum").encode())[0]
    assert a == b
    assert a[0]["type"] == "{urn:public:types}domainItemType"
