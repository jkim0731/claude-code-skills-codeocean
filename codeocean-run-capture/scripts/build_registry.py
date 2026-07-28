"""Build a machine-readable capsule registry from the CO capsule-info spreadsheet.

Reads the 'processing' sheet (capsule id / suffix / result tags / required data type /
pre-attached asset) and writes capsule_registry.json next to the skill. co_run_capture
and batch_monitor read it so `--capsule <name-or-id>` auto-fills capsule-id, the
process-name-suffix, and the result tags — instead of hand-specifying them per run.

Usage: python build_registry.py [XLSX] [OUT_JSON]
"""
import sys, json, pathlib, math
import pandas as pd

XLSX = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path("/root/capsule/code/CO_capsule_infos_260723.xlsx")
OUT  = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path(__file__).parent.parent / "capsule_registry.json"

def s(v):
    if v is None or (isinstance(v, float) and math.isnan(v)): return None
    v = str(v).strip()
    return v or None

def split_tags(v):
    v = s(v)
    return [t.strip() for t in v.split(";") if t.strip()] if v else []

def norm(name):
    return s(name).lower().replace("'", "").replace("  ", " ") if s(name) else None

df = pd.read_excel(XLSX, sheet_name="processing")
capsules, monitors = [], []
for _, r in df.iterrows():
    cid = s(r.get("capsule id")); name = s(r.get("capsule name"))
    if not cid or not name:
        continue
    typ = s(r.get("Type")) or ""
    entry = {
        "name": name, "id": cid, "type": typ, "shared": s(r.get("shared")),
        "suffix": s(r.get("suffix")),
        "tags": split_tags(r.get("result tags")),
        "required_data_type": s(r.get("required data type")),
        "pre_attached_name": s(r.get("pre-attached data asset name")),
        "pre_attached_id": s(r.get("pre-attached data asset id")),
        "git": s(r.get("Git link")), "note": s(r.get("Note")),
    }
    (monitors if "pipeline-monitor" in typ.lower() else capsules).append(entry)

# lookup indices: by id and by normalized name
by_id = {c["id"]: c for c in capsules}
by_name = {norm(c["name"]): c for c in capsules}
reg = {
    "source": str(XLSX), "n_capsules": len(capsules), "n_monitors": len(monitors),
    "capsules": capsules, "monitors": monitors,
    "index_by_id": by_id, "index_by_name": by_name,
}
OUT.write_text(json.dumps(reg, indent=2))
runnable = [c for c in capsules if c["suffix"] or c["tags"]]
print(f"wrote {OUT}")
print(f"  {len(capsules)} capsules ({len(runnable)} runnable w/ suffix+tags), {len(monitors)} pipeline-monitors")
print("\nRunnable capsules (name | id | suffix | tags | required_data_type):")
for c in runnable:
    print(f"  {c['name'][:38]:38s} | {c['id'][:8]} | {str(c['suffix']):26s} | {';'.join(c['tags'])[:30]:30s} | {c['required_data_type']}")
