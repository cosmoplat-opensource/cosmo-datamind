from pathlib import Path
import sys
import unittest

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from repair_publisher_identity import context, date_time, exported_key, same_reference


class PrivatePublisherIdentityTests(unittest.TestCase):
    def test_date_only_end_and_instant_are_end_of_day(self):
        self.assertEqual(date_time('2023-12-31', end=True), '2024-01-01T00:00:00')
        self.assertEqual(date_time('2023-12-31'), '2023-12-31T00:00:00')
        self.assertEqual(date_time('2023-12-31T12:30:00', end=True), '2023-12-31T12:30:00')

    def test_full_semantic_key_distinguishes_units_and_dimension_values(self):
        namespaces = {'ifrs': 'ifrs', 'currency': 'currency', 'scheme': 'scheme', 'axis': 'axis'}
        dims = {'concept': 'ifrs:Cost', 'entity': 'scheme:company',
                'period': '2024-01-01T00:00:00', 'unit': 'currency:GBP', 'axis:Group': 'axis:A'}
        first = exported_key(dims, namespaces)
        for key, value in (('unit', 'currency:USD'), ('axis:Group', 'axis:B')):
            changed = dict(dims, **{key: value})
            self.assertNotEqual(first, exported_key(changed, namespaces))

    def test_conflicting_published_values_are_not_selected_by_display_value(self):
        self.assertFalse(same_reference([{'value': '100'}, {'value': '200'}]))
        self.assertTrue(same_reference([{'value': '100'}, {'value': '100.00'}]))
        self.assertFalse(same_reference([]))

    def test_unsupported_typed_dimension_is_not_ignored(self):
        node = etree.fromstring('''<context xmlns="http://www.xbrl.org/2003/instance"
            xmlns:d="http://xbrl.org/2006/xbrldi"><entity><identifier scheme="s">x</identifier>
            <segment><d:typedMember dimension="d:a"><v>unresolved</v></d:typedMember></segment></entity>
            <period><instant>2023-12-31</instant></period></context>''')
        with self.assertRaises(ValueError):
            context(node)


if __name__ == '__main__':
    unittest.main()
