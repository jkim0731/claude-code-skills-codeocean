#!/usr/bin/env python
"""Validate that a Code Ocean capsule's App Panel stays in sync with its
`code/run_capsule.py` argparse.

WHY: a CO App Panel (`.codeocean/app-panel.json`) exposes parameters in the UI and
passes each one to the capsule's command as `--<key> <value>`, where the key is the
parameter's `param_name` if present, else its `name`. `run_capsule.py` reads them
with argparse. So the invariant to keep is: EVERY user-facing panel parameter's key
matches an argparse `--flag` in run_capsule.py, and their defaults/types agree.

(The CO UI writes valid schema. Some panels carry `named_parameters: true` + an
explicit `param_name` per arg; others use the hyphenated `name` directly as the key —
both are valid. This checker treats `param_name or name` as the key and does NOT
require `named_parameters`.)

This checker parses both sides STATICALLY (no imports, no capsule run) and reports
mismatches. Exit 0 = in sync, 1 = a panel param has no matching argparse flag.

Usage:
    python check_app_panel.py [capsule_dir]        # default: /lightningPose-eye-tracking
    python check_app_panel.py <capsule_dir> --json
"""
from __future__ import annotations
import argparse
import ast
import json
import pathlib
import re
import sys

DEFAULT_CAPSULE = "/lightningPose-eye-tracking"


def load_panel(capsule_dir: pathlib.Path):
    cdir = capsule_dir / ".codeocean"
    path = next((p for p in (cdir / "app-panel.json", cdir / "app_panel.json") if p.is_file()), None)
    if path is None:
        raise FileNotFoundError(f"no app-panel.json under {cdir}")
    data = json.loads(path.read_text())
    return path, data


def parse_argparse_flags(run_capsule_py: pathlib.Path) -> dict:
    """Static AST parse -> {flag_key: {default_expr, type_expr}} for every
    add_argument('--flag', ...) call (flag_key has leading dashes stripped)."""
    tree = ast.parse(run_capsule_py.read_text())
    flags = {}
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument" and node.args):
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)
                and first.value.startswith("--")):
            continue
        key = first.value.lstrip("-")
        info = {"default_expr": None, "type_expr": None}
        for kw in node.keywords:
            if kw.arg == "default":
                info["default_expr"] = ast.unparse(kw.value)
            elif kw.arg == "type":
                info["type_expr"] = ast.unparse(kw.value)
        flags[key] = info
    return flags


def resolve_module_defaults(capsule_dir: pathlib.Path) -> dict:
    """Extract `X = float(os.environ.get("X", "1.10"))`-style fallback defaults from
    utils.py so argparse defaults written as `utils.X` can be resolved."""
    out = {}
    up = capsule_dir / "code" / "utils.py"
    if up.is_file():
        pat = r'(\w+)\s*=\s*(?:float|int)\(\s*os\.environ\.get\(\s*"[^"]+"\s*,\s*"([^"]+)"\s*\)\)'
        for m in re.finditer(pat, up.read_text()):
            out[m.group(1)] = m.group(2)
    return out


def resolve_default(expr, module_defaults):
    if expr is None:
        return None
    e = expr.strip()
    if e.startswith("utils."):
        return module_defaults.get(e.split(".", 1)[1])
    try:
        return ast.literal_eval(e)
    except Exception:
        return e


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def check(capsule_dir: pathlib.Path):
    panel_path, panel = load_panel(capsule_dir)
    run_py = capsule_dir / "code" / "run_capsule.py"
    flags = parse_argparse_flags(run_py)
    mod_defaults = resolve_module_defaults(capsule_dir)

    named = bool(panel.get("named_parameters"))
    params = panel.get("parameters", []) or []

    errors, warnings, rows = [], [], []

    for prm in params:
        disp = prm.get("name")
        key = prm.get("param_name") or prm.get("name")  # key = param_name if set, else name
        panel_default = prm.get("default_value")
        vt = prm.get("value_type")

        in_argparse = key in flags
        status = "ok"
        if not in_argparse:
            errors.append(f"param '{disp}': arg key '--{key}' has NO matching argparse flag in run_capsule.py")
            status = "MISSING in argparse"
        else:
            arg_default = resolve_default(flags[key]["default_expr"], mod_defaults)
            pn, an = _num(panel_default), _num(arg_default)
            if pn is not None and an is not None:
                if pn != an:
                    warnings.append(f"param '{disp}': default {panel_default!r} != argparse default {arg_default!r}")
                    status = "default mismatch"
            elif str(panel_default) != str(arg_default):
                warnings.append(f"param '{disp}': default {panel_default!r} != argparse default {arg_default!r}")
                status = "default mismatch"
        rows.append({"display_name": disp, "arg_key": key, "value_type": vt,
                     "panel_default": panel_default,
                     "argparse_flag": f"--{key}" if in_argparse else None,
                     "argparse_default": resolve_default(flags[key]["default_expr"], mod_defaults) if in_argparse else None,
                     "status": status})

    panel_keys = {(p.get("param_name") or p.get("name")) for p in params}
    orphan_flags = [f for f in flags if f not in panel_keys]

    return {"capsule_dir": str(capsule_dir), "panel_path": str(panel_path),
            "named_parameters": named, "rows": rows, "orphan_argparse_flags": orphan_flags,
            "errors": errors, "warnings": warnings, "ok": not errors}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capsule_dir", nargs="?", default=DEFAULT_CAPSULE)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)
    res = check(pathlib.Path(a.capsule_dir))

    if a.json:
        print(json.dumps(res, indent=2, default=str))
        return 0 if res["ok"] else 1

    print(f"app-panel : {res['panel_path']}")
    print(f"run_capsule: {pathlib.Path(res['capsule_dir'])/'code'/'run_capsule.py'}")
    print(f"named_parameters: {res['named_parameters']}\n")
    hdr = f"{'display name':32s} {'arg key':32s} {'panel def':>10s} {'argparse def':>13s}  status"
    print(hdr); print("-" * len(hdr))
    for r in res["rows"]:
        print(f"{str(r['display_name']):32s} {str(r['arg_key']):32s} "
              f"{str(r['panel_default']):>10s} {str(r['argparse_default']):>13s}  {r['status']}")
    if res["orphan_argparse_flags"]:
        print(f"\nargparse flags with no panel parameter (ok if intentional/internal): "
              f"{', '.join('--'+f for f in res['orphan_argparse_flags'])}")
    for w in res["warnings"]:
        print(f"\nWARN: {w}")
    for e in res["errors"]:
        print(f"\nERROR: {e}")
    print("\n" + ("✅ app-panel is in sync with argparse" if res["ok"]
                  else "❌ app-panel does NOT match argparse — see ERROR(s) above"))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
