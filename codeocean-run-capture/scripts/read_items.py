#!/usr/bin/env python3
"""Read a list of items (session names / subject ids) from a file.

- .txt : one item per line; blank lines and `# comments` ignored.
- .csv : pick a column (by header name or 0-based index); optionally keep only
         rows matching an include-filter.

Prints one item per line (dedup preserved order). Used by run_per_session.sh /
run_per_subject.sh so "which sessions/subjects to include" can come from a CSV
(e.g. a QC / cohort table) as well as a plain list.

Examples
--------
  read_items.py sessions.txt
  read_items.py cohort.csv --column session
  read_items.py qc.csv --column session --include-col include --include-val 1
  read_items.py cohort.csv --column 0 --no-header
"""
import argparse
import csv
import pathlib
import sys

_TRUTHY = {"1", "true", "yes", "y", "t"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--column", default=None, help="CSV column: header name or 0-based index")
    ap.add_argument("--include-col", default=None, help="only keep rows where this column ...")
    ap.add_argument("--include-val", default=None,
                    help="... equals this value (default: truthy 1/true/yes)")
    ap.add_argument("--no-header", action="store_true",
                    help="CSV has no header row (use a numeric --column)")
    a = ap.parse_args()

    p = pathlib.Path(a.file)
    if not p.exists():
        sys.exit(f"read_items: file not found: {p}")

    out = []
    if p.suffix.lower() != ".csv":
        # plain text list
        for line in p.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    else:
        with open(p, newline="") as f:
            if a.no_header:
                idx = int(a.column) if (a.column and a.column.lstrip("-").isdigit()) else 0
                for row in csv.reader(f):
                    if row and idx < len(row) and row[idx].strip():
                        out.append(row[idx].strip())
            else:
                rdr = csv.DictReader(f)
                fields = rdr.fieldnames or []
                col = a.column or (fields[0] if fields else None)
                if col is None:
                    sys.exit("read_items: empty CSV / no header")
                if not col.isdigit() and col not in fields:
                    sys.exit(f"read_items: column {col!r} not found. Available: {fields}")
                if a.include_col and a.include_col not in fields:
                    sys.exit(f"read_items: include-col {a.include_col!r} not found. Available: {fields}")
                for row in rdr:
                    if a.include_col:
                        v = (row.get(a.include_col, "") or "").strip()
                        if a.include_val is not None:
                            if v != a.include_val:
                                continue
                        elif v.lower() not in _TRUTHY:
                            continue
                    if col.isdigit():
                        vals = list(row.values())
                        i = int(col)
                        val = vals[i] if i < len(vals) else None
                    else:
                        val = row.get(col)
                    if val and val.strip():
                        out.append(val.strip())

    seen = set()
    for x in out:
        if x not in seen:
            seen.add(x)
            print(x)


if __name__ == "__main__":
    main()
