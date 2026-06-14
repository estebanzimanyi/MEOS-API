"""Attach the SQL-name map (@sqlfn / @sqlop) to the MEOS-API catalog.

The catalog carries MEOS-C function names + C signatures, but bindings that
emit a SQL/UDF surface (MobilityDB SQL, MobilitySpark UDFs, MobilityDuck, …)
need the user-facing SQL name and operator. Both are machine-extractable from
the doxygen tag chain that already pervades the source:

  MEOS-C fn  --@csqlfn #MobilityDB_C()-->  MobilityDB-C wrapper
  MobilityDB-C wrapper  --@sqlfn sqlName() / @sqlop @p <op>-->  SQL name + op

So: in meos/src `@csqlfn #Wrapper()` sits above the MEOS-C function (→ MEOS-C →
Wrapper); in mobilitydb/src `@sqlfn name()` + `@sqlop @p <op>` sit above
`Datum Wrapper(PG_FUNCTION_ARGS)` (→ Wrapper → name, op). Join on Wrapper.

Adds per function (when the chain resolves): `sqlfn`, `sqlop`, `mdbC`.
"""
import re
from pathlib import Path

_CSQLFN = re.compile(r"@csqlfn\s+#(\w+)\s*\(\)")
# After the doxygen close, the MEOS-C definition: an optional return-type line
# (no parens/braces/;/=), then `name(`.
_FNDEF = re.compile(r"\*/\s*\n(?:[^\n(){};=]+\n)?(\w+)\s*\(")
_SQLFN = re.compile(r"@sqlfn\s+(\w+)\s*\(\)")
_SQLOP = re.compile(r"@sqlop\s+@p\s+(\S+)")
_DATUM = re.compile(r"Datum\s+(\w+)\s*\(\s*PG_FUNCTION_ARGS")
# `CREATE [OR REPLACE] FUNCTION name(` — the SQL-facing signature; the wrapper it
# binds is in the trailing `AS 'MODULE_PATHNAME', '<Wrapper>'`.
_CREATE_FN = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(\w+)\s*\(", re.I)
_AS_WRAPPER = re.compile(r"AS\s+'[^']*'\s*,\s*'(\w+)'", re.I)


def _split_top_commas(s):
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _wrapper_sql_sigs(sql_src):
    """MobilityDB-C wrapper name -> (sqlArity, sqlArityMax) from the CREATE FUNCTION
    definitions. The SQL signature is the binding-facing arity, NOT the C one: an arg
    with a DEFAULT clause is optional, and any trailing C param absent from the SQL form
    is a C-only out-param (e.g. the `size_t *` of the *_as_hexwkb family). So a generator
    binding the @sqlfn name must expose sqlArity..sqlArityMax args, NOT the full C list.
    Across overloads of one wrapper, take min-required / max-total."""
    out = {}
    sql_src = Path(sql_src)
    if not sql_src.exists():
        return out
    for sf in sorted(sql_src.rglob("*.sql")):
        text = sf.read_text(errors="ignore")
        for m in _CREATE_FN.finditer(text):
            i, depth, start = m.end(), 1, m.end()
            while i < len(text) and depth:
                depth += (text[i] == "(") - (text[i] == ")")
                i += 1
            wm = _AS_WRAPPER.search(text, i, i + 400)
            if not wm:
                continue  # SQL-language ($$...$$) wrapper has no C symbol — skip
            wrapper = wm.group(1)
            args = [a for a in _split_top_commas(text[start:i - 1]) if a.strip()]
            required = sum(1 for a in args if not re.search(r"\bDEFAULT\b", a, re.I))
            prev = out.get(wrapper)
            out[wrapper] = ((min(prev[0], required), max(prev[1], len(args)))
                            if prev else (required, len(args)))
    return out


def _meos_to_mdb(meos_src):
    """MEOS-C function name -> MobilityDB-C wrapper name (from @csqlfn)."""
    out = {}
    for cf in Path(meos_src).rglob("*.c"):
        text = cf.read_text(errors="ignore")
        for m in _CSQLFN.finditer(text):
            mdb_c = m.group(1)
            fm = _FNDEF.search(text, m.end())
            if fm:
                out.setdefault(fm.group(1), mdb_c)
    return out


def _mdb_to_sql(mdb_src):
    """MobilityDB-C wrapper name -> ordered list of (sqlfn, sqlop).

    A shared PG wrapper can carry more than one @sqlfn (e.g. Temporal_derivative
    is exposed as both derivative() and speed()), so collect ALL of them rather
    than the first — otherwise the mapped SQL name is order-dependent.
    """
    out = {}
    for cf in Path(mdb_src).rglob("*.c"):
        text = cf.read_text(errors="ignore")
        for m in _SQLFN.finditer(text):
            sqlfn = m.group(1)
            # @sqlop lives in the SAME doxygen block (before the closing */).
            close = text.find("*/", m.end())
            block = text[m.start():close] if close != -1 else text[m.start():m.start() + 800]
            op = _SQLOP.search(block)
            dm = _DATUM.search(text, close if close != -1 else m.end())
            if dm:
                entry = (sqlfn, op.group(1) if op else None)
                lst = out.setdefault(dm.group(1), [])
                if entry not in lst:
                    lst.append(entry)
    return out


def attach_sqlfn_map(idl, meos_src, mdb_src, sql_src=None):
    m2d = _meos_to_mdb(meos_src)
    d2s = _mdb_to_sql(mdb_src)
    w2sig = _wrapper_sql_sigs(sql_src) if sql_src else {}
    n = 0
    for f in idl["functions"]:
        mdb_c = m2d.get(f["name"])
        if not mdb_c:
            continue
        lst = d2s.get(mdb_c)
        if not lst:
            continue
        f["mdbC"] = mdb_c
        f["sqlfn"] = lst[0][0]
        # The SQL-facing arity (required..total). Lets a generator expose the SQL
        # signature instead of the wider C one: args beyond sqlArity are SQL-optional
        # (DEFAULT), and C params beyond sqlArityMax are C-only out-params.
        sig = w2sig.get(mdb_c)
        if sig:
            f["sqlArity"], f["sqlArityMax"] = sig
        if lst[0][1]:
            f["sqlop"] = lst[0][1]
        # Shared wrapper exposing >1 SQL name: record them all (binding picks).
        if len(lst) > 1:
            f["sqlfnAll"] = [s for s, _ in lst]
        n += 1
    return idl, n
