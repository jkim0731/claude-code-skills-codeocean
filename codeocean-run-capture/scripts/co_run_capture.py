#!/usr/bin/env python3
"""co_run_capture — run a Code Ocean capsule/pipeline with attached data assets
and capture its results as a named, tagged data asset.

Self-contained: depends only on the `codeocean` python client
(`pip install codeocean`). No lamf_analysis / aind-* packages required.

Auth
----
  domain: --domain, else $CODEOCEAN_DOMAIN, else https://codeocean.allenneuraldynamics.org
  token : --token,  else first of $CODEOCEAN_TOKEN, $API_SECRET, $CO_TOKEN, $CUSTOM_KEY

Subcommands
-----------
  run         attach data assets, run a capsule, (optionally) wait + capture results
              - direct mode:  we run the target capsule and capture (--capture)
              - monitor mode: (--monitor) hand the job to the aind pipeline-monitor
                capsule, which runs the target + captures results server-side
                (fire-and-forget friendly; robust for long runs). The settings are
                serialized to a JSON string parameter, capped at 4096 chars.
  capture     create a data asset from an already-finished computation
  status      print a computation's state
  find-asset  search data assets by name -> print id / name / state

Notes
-----
  * `mount` is optional; when omitted, Code Ocean mounts an asset under its own
    name (what you want in almost all cases).

Examples
--------
  # find the asset ids you need
  python co_run_capture.py find-asset --name multiplane-ophys_779891_2025-03-21

  # run a capsule with 3 assets attached, wait, and register the results
  python co_run_capture.py run \
      --capsule-id 54a4898c-01a0-4710-be33-4a528bc8b4b4 \
      --data-asset <session_id>:multiplane-ophys_779891_2025-03-21 \
      --data-asset <raw_model_id>:lightningPose-eye-model_multiplane-ophys-raw-video_2026-07-11 \
      --data-asset <clahe_model_id>:lightningPose-eye-model_multiplane-ophys-clahe-video_2026-07-11 \
      --wait --capture \
      --result-name lightningPose-eye-tracking_779891_2025-03-21 \
      --tag derived --tag multiplane-ophys --tag lp-eye --tag 779891 \
      --meta "data level=derived" --meta "experiment type=multiplane-ophys" --meta "subject id=779891"

  # monitor mode: let the aind pipeline-monitor capsule run + capture server-side
  #   (attach by name, mounts default to the asset names; fire-and-forget with --no-wait)
  python co_run_capture.py run --monitor --no-wait \
      --capsule-id 54a4898c-01a0-4710-be33-4a528bc8b4b4 \
      --data-asset-name multiplane-ophys_779891_2025-03-21_14-14-28 \
      --data-asset-name lightningPose-eye-model_multiplane-ophys-raw-video_2026-07-11 \
      --data-asset-name lightningPose-eye-model_multiplane-ophys-clahe-video_2026-07-11 \
      --process-name-suffix lp-eye \
      --tag derived --tag multiplane-ophys --tag lp-eye --tag 779891 \
      --meta "subject id=779891"

  # capture later, from an existing computation id
  python co_run_capture.py capture --computation-id <comp_id> \
      --result-name my-results --tag derived
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

DEFAULT_DOMAIN = "https://codeocean.allenneuraldynamics.org"
TOKEN_ENV_VARS = ("CODEOCEAN_TOKEN", "API_SECRET", "CO_TOKEN", "CUSTOM_KEY")

# aind-co-pipeline-monitor-capsule-all-users (capsule 5449547) — runs a target
# capsule with attached assets and captures its results, all server-side.
DEFAULT_MONITOR_CAPSULE_ID = "567b5b98-8d41-413b-9375-9ca610ca2fd3"
# Code Ocean caps a single capsule parameter string at 4096 chars.
MAX_PARAM_LEN = 4096

# recover a raw session/asset name by stripping a derived tail. AIND derived names
# look like <raw>_<process>_<YYYY-MM-DD_HH-MM-SS>; raw session ids end in a
# _<date>_<time> stamp, so keep everything up to (and including) the first one.
_RAW_SESSION_RE = re.compile(r"^(?P<raw>.+?_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})(?:_.*)?$")
_DERIVED_MARKERS = ("_processed", "_sorted", "_nwb", "_curated", "_dlc-eye", "_lp-eye")

# ── capsule registry (built from the CO capsule-info spreadsheet via build_registry.py) ──
REGISTRY_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "capsule_registry.json")


def load_registry(path=None):
    """Load the capsule registry JSON (maps name/id -> suffix/tags/required-data-type). None if absent."""
    p = path or REGISTRY_PATH
    if not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def resolve_capsule(name_or_id, path=None):
    """Resolve a capsule by exact id, exact (normalized) name, or UNIQUE name-substring."""
    reg = load_registry(path)
    if not reg:
        sys.exit(f"ERROR: capsule registry not found ({path or REGISTRY_PATH}); run build_registry.py first.")
    key = str(name_or_id).strip()
    if key in reg.get("index_by_id", {}):
        return reg["index_by_id"][key]
    nk = key.lower().replace("'", "")
    if nk in reg.get("index_by_name", {}):
        return reg["index_by_name"][nk]
    bysuf = [c for c in reg["capsules"] if (c.get("suffix") or "").lower() == nk]
    if len(bysuf) == 1:
        return bysuf[0]
    if len(bysuf) > 1:
        sys.exit("ERROR: --capsule {!r} matches multiple capsules by suffix:\n{}".format(
            name_or_id, "\n".join(f"  {c['name']}  ({c['id']})" for c in bysuf)))
    subs = [c for c in reg["capsules"] if nk in c["name"].lower()]
    if len(subs) == 1:
        return subs[0]
    if not subs:
        sys.exit(f"ERROR: no capsule matching {name_or_id!r} in the registry.")
    sys.exit("ERROR: ambiguous --capsule {!r}; matches:\n{}".format(
        name_or_id, "\n".join(f"  {c['name']}  ({c['id']})" for c in subs)))


def apply_registry(args):
    """If args.capsule is set, fill capsule_id / process_name_suffix / tag from the registry
    (explicit CLI values always win). Returns the resolved entry, or None."""
    if not getattr(args, "capsule", None):
        return None
    e = resolve_capsule(args.capsule, getattr(args, "registry", None))
    if not getattr(args, "capsule_id", None):
        args.capsule_id = e["id"]
    if getattr(args, "process_name_suffix", None) in (None, "") and e.get("suffix"):
        args.process_name_suffix = e["suffix"]
    if not getattr(args, "tag", None) and e.get("tags"):
        args.tag = list(e["tags"])
    print(f"[registry] {e['name']} -> capsule={args.capsule_id} suffix={args.process_name_suffix} "
          f"tags={args.tag}", flush=True)
    if e.get("required_data_type"):
        print(f"[registry] required data type to attach: {e['required_data_type']}", flush=True)
    if e.get("pre_attached_name"):
        print(f"[registry] pre-attached (capsule default): {e['pre_attached_name']} ({e.get('pre_attached_id')})", flush=True)
    return e


# extend derived-name markers with every registry suffix, so raw-name stripping / dedup
# prefix logic works for ANY workflow's output (HCR-ROI-label, ROICat, zdrift-qc, ...)
try:
    _reg0 = load_registry()
    if _reg0:
        _DERIVED_MARKERS = tuple(dict.fromkeys(
            tuple(_DERIVED_MARKERS) + tuple("_" + c["suffix"] for c in _reg0["capsules"] if c.get("suffix"))))
except Exception:
    pass


def raw_session_name(name):
    """Strip a derived tail so a _processed_/etc asset resolves to its raw name.

    multiplane-ophys_779891_2025-03-21_14-14-28_processed_2025-04-01_10-00-00
        -> multiplane-ophys_779891_2025-03-21_14-14-28
    Names that don't match the session pattern are returned unchanged.
    """
    if not name:
        return name
    m = _RAW_SESSION_RE.match(name)
    if m:
        return m.group("raw")
    for mk in _DERIVED_MARKERS:
        i = name.find(mk)
        if i > 0:
            return name[:i]
    return name


def _now_ts(tz):
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.now(ZoneInfo(tz or "UTC"))
    except Exception:
        dt = datetime.utcnow()
    return dt.strftime("%Y-%m-%d_%H-%M-%S")


def first_named_asset_name(args):
    """Name of the first --data-asset-name spec (used as the naming base)."""
    specs = getattr(args, "data_asset_name", None) or []
    if not specs:
        return None
    spec = specs[0]
    return (spec.split(":", 1)[0] if ":" in spec else spec).strip()


def resolve_result_name(args, base_name):
    """Captured-asset name = <raw base>_<suffix>_<date>_<time> (per-session rule).

    - explicit --result-name wins (used verbatim);
    - else, with --process-name-suffix and a known base, build the raw-based name;
    - else None (defer to the monitor's server-side naming).
    The base is the raw name of the input asset: if the input is a derived asset
    (e.g. *_processed_*), its raw session name is used instead.
    """
    if getattr(args, "result_name", None):
        return args.result_name
    suffix = getattr(args, "process_name_suffix", None)
    if suffix and base_name:
        return f"{raw_session_name(base_name)}_{suffix}_{_now_ts(getattr(args, 'name_tz', 'UTC'))}"
    return None


# ── auth ──────────────────────────────────────────────────────────────────────────
def get_client(args):
    from codeocean import CodeOcean
    domain = args.domain or os.environ.get("CODEOCEAN_DOMAIN") or DEFAULT_DOMAIN
    token = args.token
    if not token:
        for var in TOKEN_ENV_VARS:
            if os.environ.get(var):
                token = os.environ[var]
                break
    if not token:
        sys.exit(f"ERROR: no API token. Pass --token or set one of {TOKEN_ENV_VARS}.")
    return CodeOcean(domain=domain.rstrip("/"), token=token)


# ── helpers ─────────────────────────────────────────────────────────────────────────
def parse_kv(items):
    """['a=b', 'c=d e'] -> {'a':'b', 'c':'d e'}"""
    out = {}
    for it in items or []:
        if "=" not in it:
            sys.exit(f"ERROR: expected key=value, got {it!r}")
        k, v = it.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_data_asset(spec):
    """'id:mount' or 'id' -> DataAssetsRunParam"""
    from codeocean.computation import DataAssetsRunParam
    if ":" in spec:
        asset_id, mount = spec.split(":", 1)
    else:
        asset_id, mount = spec, None
    return DataAssetsRunParam(id=asset_id.strip(), mount=(mount.strip() if mount else None))


def resolve_asset_name(client, spec):
    """'name:mount' or 'name' -> DataAssetsRunParam (newest Ready match by name)."""
    from codeocean.computation import DataAssetsRunParam
    from codeocean.data_asset import DataAssetSearchParams
    if ":" in spec:
        name, mount = spec.split(":", 1)
    else:
        name, mount = spec, None
    name = name.strip()
    res = client.data_assets.search_data_assets(DataAssetSearchParams(query=name, limit=50, archived=False))
    matches = [a for a in getattr(res, "results", []) if a.name == name] or list(getattr(res, "results", []))
    if not matches:
        sys.exit(f"ERROR: no data asset found matching name {name!r}")
    # prefer Ready, then newest by created if available
    matches.sort(key=lambda a: (getattr(a, "state", "") == "ready", getattr(a, "created", 0)), reverse=True)
    chosen = matches[0]
    print(f"  resolved {name!r} -> {chosen.id} (state={getattr(chosen,'state','?')})", file=sys.stderr)
    # mount defaults to the asset's own name. On the AIND deployment the run API
    # rejects a data asset with no mount ("invalid request body") unless the asset
    # carries a default mount in its metadata — session assets don't, so we must
    # send one. (codeocean's to_dict() also serialises mount=None as `"mount":null`,
    # which is rejected too; setting it to the name avoids both.)
    return DataAssetsRunParam(id=chosen.id, mount=(mount.strip() if mount else chosen.name))


def build_source(computation_id, path):
    from codeocean.data_asset import Source, ComputationSource
    return Source(computation=ComputationSource(id=computation_id, path=path or None))


def do_capture(client, computation_id, args):
    from codeocean.data_asset import DataAssetParams
    params = DataAssetParams(
        name=args.result_name,
        tags=list(args.tag or []),
        mount=(args.result_mount or args.result_name),
        description=(args.description or None),
        source=build_source(computation_id, getattr(args, "result_path", None)),
        custom_metadata=parse_kv(args.meta) or None,
    )
    print(f"Creating data asset {args.result_name!r} from computation {computation_id} ...")
    asset = client.data_assets.create_data_asset(params)
    print(f"  data asset id: {asset.id}")
    if not args.no_wait_asset:
        asset = client.data_assets.wait_until_ready(asset, polling_interval=args.poll, timeout=args.timeout)
        print(f"  data asset state: {getattr(asset, 'state', '?')}")
    return asset


def build_monitor_json(target_capsule_id, data_assets, capture, run_extra):
    """Return a compact PipelineMonitorSettings JSON string for the monitor capsule.

    Mirrors the aind pipeline-monitor pattern: {run_params, capture_settings}.
    - data_assets: list[DataAssetsRunParam] (mount omitted where None)
    - capture: dict of CaptureSettings fields (name/tags/custom_metadata/process_name_suffix/...)
    - run_extra: dict of extra RunParams fields (version/parameters)
    Uses the aind models when available (authoritative); otherwise builds the dict
    by hand so the tool still works with only `codeocean` installed.
    """
    capture = {k: v for k, v in capture.items() if v not in (None, "", [], {})}
    try:
        from aind_codeocean_pipeline_monitor.models import PipelineMonitorSettings, CaptureSettings
        from codeocean.computation import RunParams
        settings = PipelineMonitorSettings(
            run_params=RunParams(capsule_id=target_capsule_id, data_assets=data_assets or None, **run_extra),
            capture_settings=CaptureSettings(**capture),
        )
        return settings.model_dump_json(exclude_none=True)
    except ImportError:
        run_params = {"capsule_id": target_capsule_id}
        if data_assets:
            run_params["data_assets"] = [d.model_dump(exclude_none=True) for d in data_assets]
        run_params.update(run_extra)
        return json.dumps({"run_params": run_params, "capture_settings": capture}, separators=(",", ":"))


# ── subcommands ─────────────────────────────────────────────────────────────────────
def cmd_run(args):
    from codeocean.computation import RunParams, ComputationState
    apply_registry(args)   # --capsule <name|id> -> fill capsule-id + suffix + tags from registry
    if not args.capsule_id:
        sys.exit("ERROR: provide --capsule <name|id> (registry) or --capsule-id <id>.")
    client = get_client(args)

    data_assets = [parse_data_asset(s) for s in (args.data_asset or [])]
    data_assets += [resolve_asset_name(client, s) for s in (args.data_asset_name or [])]

    # extra RunParams fields shared by both modes
    run_extra = {}
    if args.version is not None:
        run_extra["version"] = args.version
    if args.param:
        run_extra["parameters"] = list(args.param)

    # ── monitor mode: hand the whole job to the aind pipeline-monitor capsule ──────
    if args.monitor:
        if args.named_param:
            sys.exit("ERROR: --named-param is not supported with --monitor; use --param.")
        base_name = args.input_name or first_named_asset_name(args)
        capture = {
            "mount": args.result_mount,
            "description": args.description,
            "tags": list(args.tag or []),
            "custom_metadata": parse_kv(args.meta) or None,
        }
        if args.result_name:
            capture["name"] = args.result_name
            print(f"  captured asset name (explicit): {args.result_name}")
        elif args.client_name:
            # force a raw-stripped name client-side — NOTE: SUBMIT-time timestamp
            if not (args.process_name_suffix and base_name):
                sys.exit("ERROR: --client-name needs --process-name-suffix and an input name "
                         "(--input-name or --data-asset-name)")
            nm = f"{raw_session_name(base_name)}_{args.process_name_suffix}_{_now_ts(args.name_tz)}"
            capture["name"] = nm
            print(f"  captured asset name (client-side, SUBMIT time): {nm}")
        elif args.process_name_suffix:
            # default: the monitor names it at CAPTURE time — it uses the output's
            # data_description.json name (raw base, capture-time) when present, else
            # <first attached asset name>_<suffix>_<capture timestamp>.
            capture["process_name_suffix"] = args.process_name_suffix
            print(f"  captured name: server-side at CAPTURE time "
                  f"(<base>_{args.process_name_suffix}_<capture-ts>)")
        else:
            sys.exit("ERROR: --monitor requires --result-name or --process-name-suffix")
        payload = build_monitor_json(args.capsule_id, data_assets, capture, run_extra)
        if len(payload) > MAX_PARAM_LEN:
            sys.exit(f"ERROR: monitor JSON is {len(payload)} chars > {MAX_PARAM_LEN} limit — "
                     f"reduce tags/metadata/asset count.\n{payload}")
        print(f"Monitor {args.monitor_capsule_id} -> target {args.capsule_id} "
              f"({len(data_assets)} asset(s), payload {len(payload)}/{MAX_PARAM_LEN} chars)")
        comp = client.computations.run_capsule(
            RunParams(capsule_id=args.monitor_capsule_id, parameters=[payload]))
        print(f"  monitor computation id: {comp.id}")
        if args.wait:
            comp = client.computations.wait_until_completed(comp, polling_interval=args.poll, timeout=args.timeout)
            print(f"  monitor state: {getattr(comp, 'state', None)}")
            if getattr(comp, "state", None) == ComputationState.Failed:
                sys.exit(f"ERROR: monitor computation {comp.id} FAILED")
        else:
            print("  (not waiting; the monitor runs the target + captures results server-side)")
        print(comp.id)
        return

    # ── direct mode: run the target capsule ourselves, optionally capture ──────────
    if args.named_param:
        try:
            from codeocean.computation import NamedRunParam
            run_extra["named_parameters"] = [
                NamedRunParam(param_name=k, value=v) for k, v in parse_kv(args.named_param).items()
            ]
        except ImportError:
            sys.exit("ERROR: this codeocean version lacks NamedRunParam; use --param instead.")

    print(f"Running capsule {args.capsule_id} with {len(data_assets)} data asset(s) ...")
    computation = client.computations.run_capsule(
        RunParams(capsule_id=args.capsule_id, data_assets=data_assets or None, **run_extra))
    print(f"  computation id: {computation.id}")

    if not args.wait:
        print("  (not waiting; capture later with:  capture --computation-id "
              f"{computation.id} --result-name ...)")
        print(computation.id)
        return

    computation = client.computations.wait_until_completed(
        computation, polling_interval=args.poll, timeout=args.timeout)
    state = getattr(computation, "state", None)
    print(f"  computation state: {state}")
    if state == ComputationState.Failed:
        sys.exit(f"ERROR: computation {computation.id} FAILED")

    if args.capture:
        base_name = args.input_name or first_named_asset_name(args)
        resolved = resolve_result_name(args, base_name)
        if not resolved:
            sys.exit("ERROR: --capture requires --result-name (or --process-name-suffix + a known input name)")
        args.result_name = resolved
        print(f"  captured asset name: {resolved}")
        do_capture(client, computation.id, args)
    print(computation.id)


def cmd_capture(args):
    client = get_client(args)
    resolved = resolve_result_name(args, args.input_name)
    if resolved:
        args.result_name = resolved
    if not args.result_name:
        sys.exit("ERROR: capture requires --result-name (or --process-name-suffix + --input-name)")
    do_capture(client, args.computation_id, args)


def cmd_status(args):
    client = get_client(args)
    comp = client.computations.get_computation(args.computation_id)
    print(f"{comp.id}\tstate={getattr(comp,'state','?')}\tname={getattr(comp,'name','')}")


def cmd_find_asset(args):
    from codeocean.data_asset import DataAssetSearchParams
    client = get_client(args)
    res = client.data_assets.search_data_assets(
        DataAssetSearchParams(query=args.name, limit=args.limit, archived=False))
    rows = getattr(res, "results", [])
    if not rows:
        print("(no matches)")
        return
    for a in rows:
        print(f"{a.id}\t{getattr(a,'state','?')}\t{a.name}")


# ── arg parsing ──────────────────────────────────────────────────────────────────────
def add_common_auth(p):
    p.add_argument("--domain", default=None, help="Code Ocean domain (else $CODEOCEAN_DOMAIN / default)")
    p.add_argument("--token", default=None, help=f"API token (else one of {TOKEN_ENV_VARS})")


def add_capture_opts(p):
    p.add_argument("--result-name", help="explicit captured asset name (overrides suffix-based naming)")
    p.add_argument("--process-name-suffix", default=None,
                   help="suffix for the captured name: <raw input name>_<suffix>_<date>_<time>")
    p.add_argument("--input-name", default=None,
                   help="base input asset name for naming (raw name is derived from it, stripping "
                        "any _processed_/derived tail); defaults to the first --data-asset-name")
    p.add_argument("--name-tz", default="UTC", help="timezone for the name timestamp (default UTC)")
    p.add_argument("--result-mount", default=None, help="mount name for the result asset (default: result-name)")
    p.add_argument("--result-path", default=None, help="subfolder of /results to capture (default: all)")
    p.add_argument("--tag", action="append", help="tag (repeatable)")
    p.add_argument("--meta", action="append", help="custom_metadata key=value (repeatable)")
    p.add_argument("--description", default=None)
    p.add_argument("--no-wait-asset", action="store_true", help="don't wait for the asset to become Ready")


def build_parser():
    ap = argparse.ArgumentParser(description="Run a Code Ocean capsule and capture its results as a data asset.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="attach assets, run a capsule, optionally wait + capture")
    add_common_auth(r)
    r.add_argument("--capsule-id", default=None, help="target capsule id (or use --capsule to look it up)")
    r.add_argument("--capsule", default=None,
                   help="capsule name or id; looks up capsule-id + suffix + result tags in capsule_registry.json")
    r.add_argument("--registry", default=None, help="path to capsule_registry.json (default: skill dir)")
    r.add_argument("--data-asset", action="append", help="<asset_id>[:mount] (repeatable)")
    r.add_argument("--data-asset-name", action="append", help="<asset_name>[:mount], resolved via search (repeatable)")
    r.add_argument("--version", type=int, default=None, help="capsule version (optional)")
    r.add_argument("--param", action="append", help="positional parameter value (repeatable)")
    r.add_argument("--named-param", action="append", help="named parameter key=value (repeatable)")
    r.add_argument("--wait", dest="wait", action="store_true", default=True)
    r.add_argument("--no-wait", dest="wait", action="store_false")
    r.add_argument("--capture", action="store_true", help="(direct mode) capture results as a data asset after completion")
    r.add_argument("--monitor", action="store_true",
                   help="run via the aind pipeline-monitor capsule (server-side run + capture)")
    r.add_argument("--monitor-capsule-id", default=DEFAULT_MONITOR_CAPSULE_ID,
                   help="pipeline-monitor capsule id (default: aind all-users monitor)")
    r.add_argument("--client-name", action="store_true",
                   help="(monitor) force a raw-stripped name client-side (SUBMIT-time timestamp); "
                        "default is server-side CAPTURE-time naming")
    r.add_argument("--poll", type=float, default=15, help="polling interval seconds (default 15)")
    r.add_argument("--timeout", type=float, default=None, help="timeout seconds (default: none)")
    add_capture_opts(r)
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("capture", help="create a data asset from a finished computation")
    add_common_auth(c)
    c.add_argument("--computation-id", required=True)
    c.add_argument("--poll", type=float, default=15)
    c.add_argument("--timeout", type=float, default=None)
    add_capture_opts(c)
    c.set_defaults(func=cmd_capture)

    s = sub.add_parser("status", help="print a computation's state")
    add_common_auth(s)
    s.add_argument("--computation-id", required=True)
    s.set_defaults(func=cmd_status)

    f = sub.add_parser("find-asset", help="search data assets by name")
    add_common_auth(f)
    f.add_argument("--name", required=True)
    f.add_argument("--limit", type=int, default=25)
    f.set_defaults(func=cmd_find_asset)
    return ap


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
