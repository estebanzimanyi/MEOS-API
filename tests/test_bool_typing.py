"""Regression tests for the stdbool / integer-typedef stub (PR #1).

Without the stub prepended before libclang parses the MEOS headers, every
`bool` return collapses to `int` and every `bool *` out-parameter to `int *`,
silently mistyping the IDL that every downstream binding (PyMEOS-CFFI, GoMEOS,
MEOS.NET, JMEOS, MEOS.js) consumes. These tests assert the boolean shape
survives. Plain unittest, no pytest dependency.

The IDL is generated, not committed; run ``python run.py`` first.

Schema note: a function's ``returnType`` is a ``{"c", "canonical"}`` dict and a
parameter is a ``{"name", "cType", "canonical"}`` dict; output parameters are
identified by a pointer ``cType`` (there is no ``isOutput`` flag).
"""
import json
import unittest
from pathlib import Path

IDL = Path(__file__).resolve().parents[1] / "output" / "meos-idl.json"


class BoolTypingTests(unittest.TestCase):
    def setUp(self):
        if not IDL.exists():
            self.skipTest(f"{IDL} not generated; run `python run.py` first")
        idl = json.loads(IDL.read_text())
        self.by_name = {f["name"]: f for f in idl["functions"]}

    def _canonical_return(self, name):
        self.assertIn(name, self.by_name, f"{name} missing from IDL")
        return self.by_name[name]["returnType"]["canonical"]

    def test_known_bool_returners_typed_bool(self):
        # Predicate functions — pre-fix these all came back as `int`.
        for name in ("temporal_eq", "temporal_ne", "span_eq",
                     "set_eq", "contains_set_set"):
            self.assertEqual(self._canonical_return(name), "bool", name)

    def test_bool_pointer_out_param_preserved(self):
        # Compound-type case (the regex fix) — pre-fix the out-param was `int *`.
        f = self.by_name.get("tbool_value_at_timestamptz")
        self.assertIsNotNone(f, "tbool_value_at_timestamptz missing from IDL")
        bool_ptr_outs = [p for p in f["params"]
                         if "bool" in p["cType"].lower()
                         and p["cType"].rstrip().endswith("*")]
        self.assertTrue(bool_ptr_outs,
                        f"no bool* out-param found: {f['params']}")

    def test_no_bool_demoted_to_int(self):
        # Hard regression guard: if the stub is dropped or the regex breaks,
        # bool returners collapse toward int. 614 at the time of writing.
        bool_returners = [f for f in self.by_name.values()
                          if f["returnType"]["canonical"] == "bool"]
        self.assertGreater(len(bool_returners), 500,
                           "bool returners collapsed toward int — "
                           "stdbool stub regression?")


if __name__ == "__main__":
    unittest.main()
