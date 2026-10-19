"""Unit tests for store loading, legacy upgrades, and the migrate CLI."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python-sdk"))

from voice_memory.store import JsonFileStore, STORE_VERSION, upgrade_legacy_store  # noqa: E402
from voice_memory import migrate  # noqa: E402


def tmp_path(name="store.json"):
    return os.path.join(tempfile.mkdtemp(), name)


LEGACY = {
    "version": 1,
    "contacts": {
        "+15550001111": {
            "contact_id": "+15550001111",
            "display_name": "Sam",
            "preferences": [{"text": "prefers text updates", "source": "call",
                             "created_at": "2026-10-01T00:00:00",
                             "valid_until": "", "tags": []}],
            "facts": [],
            "open_threads": [{"text": "waiting on refund", "source": "call",
                               "created_at": "2026-10-02T00:00:00",
                               "valid_until": "", "tags": []}],
            "resolved": [{"text": "resolved: login fixed", "source": "call",
                          "created_at": "2026-10-03T00:00:00",
                          "valid_until": "2026-11-03T00:00:00", "tags": []}],
            "call_count": 3,
            "last_call_at": "2026-10-03T00:00:00",
            "summary": "Prefers: prefers text updates",
        },
    },
}


class TestStore(unittest.TestCase):
    def test_roundtrip(self):
        p = tmp_path()
        s = JsonFileStore(p)
        s.set_caller("a", {"callerId": "a", "callCount": 1})
        self.assertEqual(JsonFileStore(p).get_caller("a")["callCount"], 1)

    def test_missing_file_starts_empty(self):
        s = JsonFileStore(tmp_path())
        self.assertEqual(s.list_callers(), [])

    def test_delete(self):
        p = tmp_path()
        s = JsonFileStore(p)
        s.set_caller("a", {"callerId": "a"})
        self.assertTrue(s.delete_caller("a"))
        self.assertFalse(s.delete_caller("a"))

    def test_version_2_written(self):
        p = tmp_path()
        JsonFileStore(p).set_caller("a", {"callerId": "a"})
        self.assertEqual(json.load(open(p))["version"], 2)
        self.assertEqual(STORE_VERSION, 2)

    def test_corrupt_file_starts_empty(self):
        p = tmp_path()
        open(p, "w").write("not json{{{")
        self.assertEqual(JsonFileStore(p).list_callers(), [])


class TestLegacyUpgrade(unittest.TestCase):
    def test_contacts_shape_upgrades(self):
        data = upgrade_legacy_store(LEGACY)
        self.assertEqual(data["version"], 2)
        # legacy keys normalize on upgrade
        b = data["callers"]["5550001111"]
        self.assertEqual(b["callerId"], "5550001111")
        self.assertEqual(b["displayName"], "Sam")
        self.assertEqual(b["callCount"], 3)
        self.assertEqual(b["openThreads"][0]["status"], "open")
        self.assertEqual(b["resolvedItems"][0]["expiresAt"], "2026-11-03T00:00:00")
        self.assertEqual(b["summary"], "Prefers: prefers text updates")

    def test_colliding_keys_merge(self):
        legacy = {"version": 1, "contacts": {
            "+15550001111": {**LEGACY["contacts"]["+15550001111"], "call_count": 1,
                             "preferences": []},
            "5550001111": {**LEGACY["contacts"]["+15550001111"], "call_count": 2,
                           "preferences": []},
        }}
        data = upgrade_legacy_store(legacy)
        self.assertEqual(list(data["callers"].keys()), ["5550001111"])
        self.assertEqual(data["callers"]["5550001111"]["callCount"], 3)

    def test_v1_callers_shape_passes_through(self):
        data = upgrade_legacy_store({"version": 1, "callers": {"a": {"callerId": "a"}}})
        self.assertEqual(data["callers"]["a"]["callerId"], "a")

    def test_load_upgrades_legacy_file(self):
        p = tmp_path()
        json.dump(LEGACY, open(p, "w"))
        s = JsonFileStore(p)
        self.assertEqual(s.list_callers(), ["5550001111"])

    def test_migrate_cli(self):
        src = tmp_path("legacy.json")
        dst = tmp_path("new.json")
        json.dump(LEGACY, open(src, "w"))
        self.assertEqual(migrate.main([src, dst]), 0)
        data = json.load(open(dst))
        self.assertEqual(data["version"], 2)
        self.assertIn("5550001111", data["callers"])

    def test_migrate_cli_in_place(self):
        src = tmp_path("legacy.json")
        json.dump(LEGACY, open(src, "w"))
        self.assertEqual(migrate.main([src]), 0)
        self.assertIn("5550001111", json.load(open(src))["callers"])


if __name__ == "__main__":
    unittest.main()
