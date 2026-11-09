import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from native_registry_metadata_v24 import discover_links, validate_url


def test_collector_only_allows_official_registry_metadata_not_report_assets():
    assert validate_url("https://api.gleif.org/api/v1/lei-records/2138001P49OLAEU33T68")
    assert validate_url("https://api.gleif.org/api/v1/relationship-records/ABC-DEF")
    for url in ("file:///tmp/report.xhtml", "https://example.com/api/v1/lei-records/A", "https://api.gleif.org/report.xhtml",
                "https://api.gleif.org/api/v1/lei-records/../../report.xhtml", "https://api.gleif.org/api/v1/lei-records/A?report=1",
                "https://user:password@api.gleif.org/api/v1/lei-records/A"):
        with pytest.raises(ValueError, match="metadata endpoints"):
            validate_url(url)


def test_direct_parent_exceptions_and_new_fund_and_successor_links_are_retained():
    base = "https://api.gleif.org/api/v1/lei-records/A/"
    response = {"data": {"relationships": {
        "direct-parent": {"links": {"reporting-exception": base + "direct-parent-reporting-exception"}},
        "fund-manager": {"links": {"lei-record": base + "fund-manager", "relationship-record": base + "fund-manager-relationship"}},
        "successor-entities": {"links": {"related": base + "successor-entities"}},
        "direct-children": {"links": {"related": base + "direct-children"}},
    }}}
    links, unknown = discover_links(response)
    assert len(links) == 4 and not unknown
    assert any(r["link_type"] == "reporting-exception" for r in links)
    assert not any(r["relation"] == "direct-children" for r in links)


def test_missing_and_unsupported_relationships_remain_unresolved_without_fetching_them():
    assert discover_links({"data": {"relationships": {}}}) == ([], [])
    requests, unknown = discover_links({"data": {"relationships": {"ultimate-parent": {
        "links": {"related": "https://filings.example/report.xhtml"}}}}})
    assert not requests and len(unknown) == 1
