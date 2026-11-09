import tarfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from fintab_scope_prepare_v10 import coverage, identity, image_to_pdf, safe_regular, word_owner


def cell(rows, cols, header=False, projected=False):
    return {"row_nums": rows, "column_nums": cols, "is_column_header": header,
            "is_projected_row_header": projected}


def test_multilevel_header_membership_preserves_partial_unknown():
    parent = cell([0], [1, 2], True)
    assert coverage(parent, cell([2], [1])) is True
    assert coverage(parent, cell([2], [3])) is False
    assert coverage(parent, cell([2], [2, 3])) is None
    assert coverage(parent, cell([1], [1], True)) is None
    assert coverage(parent, cell([2], [1], projected=True)) is None


def test_affine_geometry_and_ambiguous_boundaries():
    assert image_to_pdf([10, 20, 50, 60], [0, 0, 100, 100], [20, 40, 220, 240]) == [40, 80, 120, 160]
    cells = [{"pdf_bbox": [0, 0, 10, 10]}, {"pdf_bbox": [10, 0, 20, 10]}]
    assert word_owner([1, 1, 3, 3], cells) == 0
    assert word_owner([9, 1, 11, 3], cells) is None
    assert word_owner([30, 1, 32, 3], cells) is None


def test_archive_paths_and_links_cannot_escape():
    good = tarfile.TarInfo("FinTabNet.c-Structure/images/a.jpg")
    assert safe_regular(good)
    for name in ("/a.jpg", "../a.jpg", "root/../a.jpg"):
        with pytest.raises(ValueError):
            safe_regular(tarfile.TarInfo(name))
    link = tarfile.TarInfo("a.jpg")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../a.jpg"
    with pytest.raises(ValueError):
        safe_regular(link)


def test_publisher_image_identity_must_match():
    row = {"document_id": "VLO_2016_page_61", "document_table_index": 0,
           "structure_id": "VLO_2016_page_61_0", "split": "train"}
    assert identity(row)["table_id"] == "VLO_2016_page_61_table_0"
    row["structure_id"] = "VLO_2016_page_61_1"
    with pytest.raises(ValueError):
        identity(row)
