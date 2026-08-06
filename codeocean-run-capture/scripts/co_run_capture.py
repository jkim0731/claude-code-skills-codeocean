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
  describe-params
              inspect a target's PARAMETER CONFIGURATION — detect capsule vs
              pipeline, list the app-panel parameters (param_name/default), and
              print whether to pass them FLAT (positional --param) or NAMED
              (--named-param param_name=value).

Parameter configuration (flat vs named vs positional)
-----------------------------------------------------
  Capsules and pipelines are both reachable via /capsules/{id}, but they consume
  parameters DIFFERENTLY, and a pipeline SILENTLY IGNORES flat positional
  parameters (the run "succeeds" with default values). So `run` now:
    1. detects the target kind (auto): a pipeline has a `versions` array and no
       `cloned_from_url`; a capsule has `cloned_from_url`. Override with --kind.
    2. routes parameters (--param-mode auto): pipeline -> NAMED; capsule -> FLAT.
       For a pipeline, flat --param values are auto-mapped onto the app-panel
       param_names by order (count must match), or pass --named-param directly.
    3. runs pipelines via RunParams.pipeline_id (use --pipeline-id, or --kind
       pipeline), capsules via capsule_id.
    4. VERIFIES after submit that the values we requested actually landed on the
       computation, and warns loudly on any mismatch (--no-verify-params to skip).
  Use `describe-params` first when unsure how a target wants its parameters.

Notes
-----
  * `mount` is optional; when omitted, Code Ocean mounts an asset under its own
    name (what you want in almost all cases).
  * Fixed assets baked into a pipeline (models, schemas) are attached
    automatically — do NOT re-attach them or the API rejects the run with
    "data asset already attached"; pass only the variable input(s).
  * Captured assets are SHARED with everyone as `viewer` by default (matching the
    Code Ocean UI capture default; the owner is kept). Use --private to keep it
    private, or --share-role discoverable/none. (Direct-mode capture only; in
    --monitor mode the aind pipeline-monitor controls capture/sharing.)

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


def share_everyone(client, asset_id, role="viewer"):
    """Share a captured asset with everyone — mirrors the Code Ocean UI capture default (which
    shares the result). Keeps the owner intact. role: 'viewer' (readable by all), 'discoverable'
    (searchable only), or 'none' (private). Verifiable via GET data_assets/{id}/permissions."""
    from codeocean.components import Permissions, EveryoneRole
    try:
        client.data_assets.update_permissions(asset_id, Permissions(everyone=EveryoneRole(role)))
        print(f"  shared with everyone (everyone={role})")
    except Exception as e:
        print(f"  WARNING: could not set sharing (everyone={role}): {e}")


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
    if not getattr(args, "private", False):
        share_everyone(client, asset.id, getattr(args, "share_role", "viewer"))
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


# ── parameter-configuration checking (flat vs named vs positional) ────────────────────
def detect_target_kind(client, capsule_id=None, pipeline_id=None, kind="auto"):
    """Return (target_id, kind, reason) with kind in {'capsule','pipeline'}.

    Pipelines and capsules are BOTH reachable via /capsules/{id} and the app-panel
    endpoints, and a pipeline's flat positional `parameters` are silently IGNORED at
    run time (you must pass NAMED parameters), so we must know which we're targeting.
    Discriminator (confirmed on the AIND deployment): a pipeline's /capsules/{id}
    payload carries a `versions` array and NO `cloned_from_url` (it is built in Code
    Ocean, not git-cloned); a plain capsule has `cloned_from_url` and no `versions`.
    """
    if pipeline_id:
        return pipeline_id, "pipeline", "explicit --pipeline-id"
    tid = capsule_id
    if kind in ("capsule", "pipeline"):
        return tid, kind, f"forced via --kind {kind}"
    try:
        raw = client.session.get(f"capsules/{tid}").json()
    except Exception as e:
        return tid, "capsule", f"auto-detect failed ({e}); assuming capsule"
    if ("versions" in raw) and ("cloned_from_url" not in raw):
        return tid, "pipeline", "no cloned_from_url + has versions (Code-Ocean-built pipeline)"
    return tid, "capsule", "has cloned_from_url (git-cloned capsule)"


def fetch_param_config(client, tid):
    """Return the target's app-panel parameter list as ordered dicts.

    [{idx, param_name, name, value_type, default_value, category}, ...]  (may be []).
    """
    ap = client.capsules.get_capsule_app_panel(tid)
    d = ap.to_dict() if hasattr(ap, "to_dict") else ap
    cats = {c.get("id"): c.get("name") for c in (d.get("categories") or [])}
    out = []
    for i, p in enumerate(d.get("parameters") or []):
        out.append({
            "idx": i,
            "param_name": p.get("param_name"),
            "name": p.get("name"),
            "value_type": str(p.get("value_type")),
            "default_value": p.get("default_value"),
            "category": cats.get(p.get("category"), p.get("category")),
        })
    return out


def recommended_param_mode(kind, cfg):
    """'named' | 'flat' | 'positional-cli' — how params must be passed to this target."""
    if kind == "pipeline":
        return "named"          # pipelines IGNORE flat positional parameters
    return "flat" if cfg else "positional-cli"


def build_param_plan(client, tid, kind, args):
    """Route --param / --named-param into RunParams fields for this target's mode.

    Returns (run_extra_params: dict, intended_named: dict, message: str). `intended_named`
    (param_name -> value) is what we can later verify actually landed on the computation.
    """
    from codeocean.computation import NamedRunParam
    mode = args.param_mode
    if mode == "auto":
        mode = "named" if kind == "pipeline" else "flat"

    run_extra, intended, msgs = {}, {}, []

    if args.named_param:
        nd = parse_kv(args.named_param)
        run_extra["named_parameters"] = [NamedRunParam(param_name=k, value=v) for k, v in nd.items()]
        intended.update(nd)
        msgs.append(f"NAMED parameters ({len(nd)}) from --named-param")
        if args.param:
            msgs.append("WARNING: ignoring --param because --named-param was given")
    elif args.param:
        if mode == "named":
            # pipeline (or forced named): map flat positional values onto app-panel param_names
            cfg = fetch_param_config(client, tid)
            if not cfg:
                sys.exit("ERROR: target needs NAMED parameters but exposes no app-panel params to map "
                         "flat --param onto; pass --named-param param_name=value instead.")
            if len(args.param) != len(cfg):
                sys.exit(f"ERROR: --param count {len(args.param)} != app-panel param count {len(cfg)}; "
                         f"cannot safely map flat->named. Use --named-param, or inspect with "
                         f"`co_run_capture.py describe-params`.")
            nd = {cfg[i]["param_name"]: v for i, v in enumerate(args.param)}
            run_extra["named_parameters"] = [NamedRunParam(param_name=k, value=v) for k, v in nd.items()]
            intended.update(nd)
            msgs.append(f"kind=pipeline -> mapped {len(nd)} flat --param values onto NAMED parameters by app-panel order")
        else:
            run_extra["parameters"] = list(args.param)
            msgs.append(f"FLAT positional parameters ({len(args.param)})")
            # best-effort: if an app panel is present, remember intended values for verification
            cfg = fetch_param_config(client, tid)
            if cfg and len(cfg) == len(args.param):
                intended = {cfg[i]["param_name"]: v for i, v in enumerate(args.param)}
    else:
        msgs.append("no parameters passed (target defaults apply)")

    return run_extra, intended, "; ".join(msgs)


def verify_applied_params(client, comp_id, intended):
    """Fetch the submitted computation and confirm each intended param_name landed with
    the requested value. Prints a prominent warning on any mismatch (the classic
    flat-positional-ignored-by-a-pipeline failure) and returns True iff all matched."""
    if not intended:
        return True
    try:
        comp = client.computations.get_computation(comp_id)
    except Exception as e:
        print(f"  [params] could not verify applied parameters: {e}")
        return True
    resolved = {p.param_name: ("" if p.value is None else str(p.value)) for p in (comp.parameters or [])}
    mism = [(k, v, resolved.get(k, "<absent>")) for k, v in intended.items()
            if str(resolved.get(k, "<absent>")) != str(v)]
    if mism:
        print(f"  !! PARAMETER CHECK FAILED for computation {comp_id} — {len(mism)} requested value(s) did NOT apply")
        print("     (likely a flat-vs-named mismatch; pipelines silently ignore flat positional parameters):")
        for k, want, got in mism[:12]:
            print(f"       {k}: requested {want!r} -> resolved {got!r}")
        print("     -> inspect the target with `describe-params`; use --named-param for pipelines. "
              "Consider stopping this computation and re-running.")
        return False
    print(f"  [params] verified {len(intended)} parameter value(s) applied on the computation")
    return True


# ── subcommands ─────────────────────────────────────────────────────────────────────
def cmd_describe_params(args):
    """Inspect a capsule/pipeline's parameter configuration and print how to pass params."""
    client = get_client(args)
    apply_registry(args)   # allow --capsule <name|id>
    if not (args.capsule_id or args.pipeline_id):
        sys.exit("ERROR: provide --capsule <name|id>, --capsule-id <id>, or --pipeline-id <id>.")
    tid, kind, reason = detect_target_kind(client, args.capsule_id, args.pipeline_id, args.kind)
    cfg = fetch_param_config(client, tid)
    mode = recommended_param_mode(kind, cfg)
    print(f"target {tid}")
    print(f"  detected kind : {kind}   ({reason})")
    print(f"  app-panel params: {len(cfg)}")
    print(f"  PARAMETER MODE : {mode.upper()}")
    if mode == "named":
        print("    -> pipelines IGNORE flat positional --param; pass --named-param param_name=value")
    elif mode == "flat":
        print("    -> pass flat --param values in the idx order below, or --named-param param_name=value")
    else:
        print("    -> no app-panel params; capsule takes positional CLI args (flat --param), order per its code")
    if cfg:
        print(f"\n  {'idx':>3}  {'param_name':34s} {'value_type':10s} {'default':22s} category")
        for p in cfg:
            print(f"  {p['idx']:>3}  {str(p['param_name']):34s} {str(p['value_type']):10s} "
                  f"{str(p['default_value'])[:22]:22s} {p['category']}")
        if mode == "named":
            print("\n  example:")
            for p in cfg[:3]:
                print(f"    --named-param {p['param_name']}={p['default_value']}")


def cmd_run(args):
    from codeocean.computation import RunParams, ComputationState
    apply_registry(args)   # --capsule <name|id> -> fill capsule-id + suffix + tags from registry
    if not (args.capsule_id or args.pipeline_id):
        sys.exit("ERROR: provide --capsule <name|id> (registry), --capsule-id <id>, or --pipeline-id <id>.")
    client = get_client(args)

    data_assets = [parse_data_asset(s) for s in (args.data_asset or [])]
    data_assets += [resolve_asset_name(client, s) for s in (args.data_asset_name or [])]

    # detect capsule-vs-pipeline and route parameters into the correct RunParams field
    # (pipelines silently IGNORE flat positional parameters — they need NAMED ones).
    tid, kind, reason = detect_target_kind(client, args.capsule_id, args.pipeline_id, args.kind)
    print(f"[target] {tid}  kind={kind}  ({reason})")
    run_extra, intended, pmsg = build_param_plan(client, tid, kind, args)
    if args.version is not None:
        run_extra["version"] = args.version
    print(f"[params] {pmsg}")

    # ── monitor mode: hand the whole job to the aind pipeline-monitor capsule ──────
    if args.monitor:
        if kind == "pipeline":
            print("  WARNING: --monitor launches the target via capsule_id; running a PIPELINE through "
                  "the monitor may not execute it as a pipeline. Prefer direct mode (drop --monitor).")
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
        payload = build_monitor_json(tid, data_assets, capture, run_extra)
        if len(payload) > MAX_PARAM_LEN:
            sys.exit(f"ERROR: monitor JSON is {len(payload)} chars > {MAX_PARAM_LEN} limit — "
                     f"reduce tags/metadata/asset count.\n{payload}")
        print(f"Monitor {args.monitor_capsule_id} -> target {tid} "
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

    # ── direct mode: run the target ourselves, optionally capture ──────────────────
    id_kw = {"pipeline_id": tid} if kind == "pipeline" else {"capsule_id": tid}
    print(f"Running {kind} {tid} with {len(data_assets)} data asset(s) ...")
    computation = client.computations.run_capsule(
        RunParams(data_assets=data_assets or None, **id_kw, **run_extra))
    print(f"  computation id: {computation.id}")

    # verify the parameters we requested actually applied (catches flat-vs-named mismatch)
    if intended and not args.no_verify_params:
        verify_applied_params(client, computation.id, intended)

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
    p.add_argument("--private", action="store_true",
                   help="do NOT share the captured asset (default: share with everyone as viewer, "
                        "matching the Code Ocean UI capture default)")
    p.add_argument("--share-role", default="viewer", choices=["viewer", "discoverable", "none"],
                   help="everyone-access level for the captured asset (default: viewer)")


def build_parser():
    ap = argparse.ArgumentParser(description="Run a Code Ocean capsule and capture its results as a data asset.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="attach assets, run a capsule, optionally wait + capture")
    add_common_auth(r)
    r.add_argument("--capsule-id", default=None, help="target capsule id (or use --capsule to look it up)")
    r.add_argument("--pipeline-id", default=None,
                   help="target PIPELINE id (runs via RunParams.pipeline_id; implies --kind pipeline)")
    r.add_argument("--kind", choices=["auto", "capsule", "pipeline"], default="auto",
                   help="target type; 'auto' detects capsule vs pipeline (default auto)")
    r.add_argument("--param-mode", choices=["auto", "flat", "named"], default="auto",
                   help="how to pass --param: auto picks named for pipelines / flat for capsules (default auto)")
    r.add_argument("--no-verify-params", action="store_true",
                   help="skip the post-submit check that requested parameter values actually applied")
    r.add_argument("--capsule", default=None,
                   help="capsule name or id; looks up capsule-id + suffix + result tags in capsule_registry.json")
    r.add_argument("--registry", default=None, help="path to capsule_registry.json (default: skill dir)")
    r.add_argument("--data-asset", action="append", help="<asset_id>[:mount] (repeatable)")
    r.add_argument("--data-asset-name", action="append", help="<asset_name>[:mount], resolved via search (repeatable)")
    r.add_argument("--version", type=int, default=None, help="capsule/pipeline version (optional)")
    r.add_argument("--param", action="append",
                   help="parameter value: flat positional for capsules; for pipelines these are auto-mapped "
                        "onto app-panel param_names by order (repeatable)")
    r.add_argument("--named-param", action="append", help="named parameter param_name=value (repeatable)")
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

    d = sub.add_parser("describe-params",
                       help="inspect a capsule/pipeline's parameter configuration (flat vs named) + how to pass params")
    add_common_auth(d)
    d.add_argument("--capsule-id", default=None, help="target capsule id")
    d.add_argument("--pipeline-id", default=None, help="target pipeline id")
    d.add_argument("--capsule", default=None, help="capsule name or id (registry lookup)")
    d.add_argument("--registry", default=None, help="path to capsule_registry.json (default: skill dir)")
    d.add_argument("--kind", choices=["auto", "capsule", "pipeline"], default="auto",
                   help="target type; 'auto' detects capsule vs pipeline (default auto)")
    d.set_defaults(func=cmd_describe_params, process_name_suffix=None, tag=None)
    return ap


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
