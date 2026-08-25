#!/usr/bin/env python3
"""
Generate .co-registry.json from a capsule info XLSX file.

A registry is a JSON file that maps friendly capsule names to their UUIDs and
standard processing conventions (suffix, tags, etc.). This enables teams to
enforce naming standards without hardcoding personal data in the skill.

Usage:
  python build_registry.py /path/to/CO_capsule_infos.xlsx .co-registry.json

The XLSX file should have a 'processing' sheet with columns:
  - capsule name: friendly name (used for --capsule lookups)
  - capsule id: UUID
  - suffix: standard suffix for derived assets (e.g., 'lp-eye', 'rorcat')
  - result tags: semicolon-separated tags to auto-apply to results
  - Type: capsule type (for documentation)
  - required data type: input data type requirement
  - pre-attached data asset name: (optional) baked-in asset reference
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime


def build_registry(xlsx_path: str, output_path: str) -> None:
    """Convert XLSX → .co-registry.json"""
    try:
        import pandas as pd
    except ImportError:
        print("ERROR: pandas not installed. Install with: pip install pandas openpyxl")
        sys.exit(1)

    xlsx_path_obj = Path(xlsx_path)
    if not xlsx_path_obj.exists():
        print(f"ERROR: XLSX file not found: {xlsx_path}")
        sys.exit(1)

    try:
        df = pd.read_excel(xlsx_path, sheet_name='processing')
    except Exception as e:
        print(f"ERROR: Could not read 'processing' sheet from {xlsx_path}: {e}")
        sys.exit(1)

    capsules = []
    for idx, row in df.iterrows():
        capsule = {
            "name": row.get('capsule name'),
            "id": row.get('capsule id'),
            "type": row.get('Type'),
            "suffix": row.get('suffix') if pd.notna(row.get('suffix')) else None,
            "tags": (
                [t.strip() for t in str(row.get('result tags', '')).split(';') if t.strip()]
                if pd.notna(row.get('result tags'))
                else []
            ),
            "required_data_type": row.get('required data type') if pd.notna(row.get('required data type')) else None,
            "pre_attached_name": row.get('pre-attached data asset name') if pd.notna(row.get('pre-attached data asset name')) else None,
        }
        capsules.append(capsule)

    # Build lookup indices for fast queries
    index_by_id = {c["id"]: c for c in capsules if c["id"]}
    index_by_name = {c["name"]: c for c in capsules if c["name"]}

    registry = {
        "source": str(xlsx_path_obj.absolute()),
        "generated_at": datetime.now().isoformat(),
        "capsule_count": len(capsules),
        "capsules": capsules,
        "_index_by_id": index_by_id,
        "_index_by_name": index_by_name,
    }

    with open(output_path, 'w') as f:
        json.dump(registry, f, indent=2)

    print(f"✅ Registry generated: {output_path} ({len(capsules)} capsules)")
    print(f"   Source: {xlsx_path}")
    print(f"   Use with: python co_run_capture.py run --registry {output_path} --capsule <name> ...")


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('xlsx_path', help='Path to CO_capsule_infos*.xlsx file')
    parser.add_argument('output_path', help='Output .co-registry.json path')
    args = parser.parse_args()

    build_registry(args.xlsx_path, args.output_path)
