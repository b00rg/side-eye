"""The Python code and scripts/tiger_schema.sql must agree. No database needed."""

import importlib.util
import re
import sys
import unittest
from pathlib import Path

from alarm_store import COLUMNS, INSERT_SQL, LABEL_SQL

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = (ROOT / "scripts" / "tiger_schema.sql").read_text()


def table_columns():
    body = re.search(r"CREATE TABLE IF NOT EXISTS events \((.*?)\n\);", SCHEMA, re.S).group(1)
    names = []
    for line in body.splitlines():
        line = line.split("--", 1)[0].strip()
        if line and not line.startswith(("PRIMARY KEY", "CONSTRAINT")):
            names.append(line.split()[0])
    return names


class SchemaMatchesCodeTests(unittest.TestCase):
    def test_every_uploaded_column_exists_in_the_table(self):
        missing = set(COLUMNS) - set(table_columns())
        self.assertEqual(missing, set())

    def test_every_table_column_is_uploaded(self):
        self.assertEqual(set(table_columns()) - set(COLUMNS), set())

    def test_true_negatives_are_blocked_by_the_database(self):
        self.assertIn("CHECK (buzzed OR glasses_present IS TRUE)", SCHEMA)

    def test_reuploads_are_idempotent(self):
        self.assertIn("PRIMARY KEY (event_id, time)", SCHEMA)
        self.assertTrue(INSERT_SQL.endswith("ON CONFLICT DO NOTHING"))

    def test_label_updates_by_full_primary_key(self):
        self.assertIn("WHERE event_id = %s AND time = %s", LABEL_SQL)

    def test_outcomes_view_is_recreated_so_new_columns_fit(self):
        # CREATE OR REPLACE fails once events gains columns (they land before `outcome`).
        drop, create = SCHEMA.index("DROP VIEW IF EXISTS event_outcomes"), SCHEMA.index("CREATE VIEW event_outcomes")
        self.assertLess(drop, create)
        self.assertLess(SCHEMA.index("ADD COLUMN IF NOT EXISTS held"), create)

    def test_outcomes_view_covers_all_cases(self):
        for outcome in ("'TP'", "'FP'", "'FN'", "'pending'"):
            self.assertIn(outcome, SCHEMA)

    def test_privacy_policies_are_in_the_schema(self):
        self.assertIn("add_retention_policy('events', INTERVAL '30 days'", SCHEMA)
        self.assertIn("add_retention_policy('events_hourly', INTERVAL '30 days'", SCHEMA)
        for banned in ("mac", "address", "latitude", "longitude", "location"):
            self.assertNotIn(banned, [c.lower() for c in table_columns()])

    def test_scripts_use_the_events_table(self):
        for name in ("annotate.py", "tiger_setup.py"):
            text = (ROOT / "scripts" / name).read_text()
            self.assertNotRegex(text, r"\bFROM alarms\b|\bINTO alarms\b|\bUPDATE alarms\b", name)


@unittest.skipUnless(importlib.util.find_spec("psycopg"), "psycopg not installed")
class SetupScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(ROOT / "scripts"))
        import tiger_setup
        cls.statements = staticmethod(tiger_setup.statements)

    def test_schema_splits_into_clean_statements(self):
        statements = self.statements(SCHEMA)
        self.assertGreaterEqual(len(statements), 10)
        for statement in statements:
            self.assertNotIn("--", statement)
            self.assertRegex(statement, r"^(CREATE|SELECT|ALTER|DROP VIEW)\b", statement[:60])   # never DROP TABLE

    def test_semicolons_inside_comments_do_not_split(self):
        sql = "CREATE TABLE t (a int -- one; two\n);\nSELECT 1;"
        self.assertEqual(self.statements(sql), ["CREATE TABLE t (a int \n)", "SELECT 1"])


if __name__ == "__main__":
    unittest.main()
