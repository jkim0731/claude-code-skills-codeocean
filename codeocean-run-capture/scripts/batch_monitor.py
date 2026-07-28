#!/usr/bin/env python3
"""batch_monitor — submit many capsule runs through the pipeline-monitor with a
concurrency cap and live submitted/finished/failed tracking.

Mirrors the aind pipeline-monitor capsule's control loop: keep at most --max-jobs
monitor computations in flight (initializing/running); as each finishes, submit
the next; track and print counts throughout. Resumable via a state CSV.

Each item (a session name, or `id:mount`, or `name`) becomes one monitor run that
launches the target capsule (session attached, mount defaulted to the asset name)
and captures results server-side as `<raw name>_<suffix>_<capture-ts>`.

Per item, the subject is auto-derived from a `multiplane-ophys_<subject>_<date>_<time>`
name and added BOTH as a tag and as `subject id` custom_metadata (disable with
--no-subject-tag). `--meta key=value` adds fixed custom_metadata to every item.

--skip-existing skips items that already have a READY result from this capsule
(matched on name `<session>_<suffix>_*` + provenance.capsule + the input session in
provenance.data_assets). Add --require-data-asset <model_id> (repeatable) and/or
--require-commit <sha> to also require matching models / code history before skipping.
Works with --dry-run to preview exactly what would run vs. be skipped (no launches).

Usage:
  python batch_monitor.py --capsule-id 54a4898c-... --items-file sessions.txt \
      --max-jobs 10 --poll 120 --process-name-suffix lp-eye \
      --tag derived --tag multiplane-ophys --tag lp-eye
      # -> each item also gets tag <subject> + meta "subject id=<subject>" automatically

  # items from a CSV column, with an include filter:
  python batch_monitor.py --capsule-id ... --items-file cohort.csv \
      --column session --include-col include --process-name-suffix lp-eye

Auth/domain: same env as co_run_capture.py ($CODEOCEAN_TOKEN/$API_SECRET, $CODEOCEAN_DOMAIN).
Billable: launches runs + creates data assets.
"""
from __future__ import annotations
import argparse, csv, os, pathlib, sys, time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import co_run_capture as crc  # reuse build_monitor_json / raw_session_name

ACTIVE = {"initializing", "running", "finalizing"}
DONE = {"completed", "failed"}


def classify_outcome(comp):
    """True success of a finished monitor computation — do NOT trust `.state`/`.end_status`.

    The aind pipeline-monitor capsule reports state='completed' AND end_status='succeeded'
    even when it raised because its TARGET run failed and nothing was captured. Empirically
    (2026-07-24, same-batch good vs bad monitors) the ONLY reliable field on the computation
    is **exit_code** (0 = captured, 1 = raised/no-capture). `has_results` is False for BOTH
    good and bad (the monitor writes only its own `output` log; the scientific output is a
    SEPARATE captured data asset) — so it must NOT be used as a gate. `end_status` lies too.
    The authoritative confirmation is that a captured data asset actually exists — enable
    --verify-capture (default on) for that second, independent gate.

    Returns (outcome, reason) where outcome in {None(still running), 'completed', 'failed'}.
    """
    if comp is None:
        return "failed", "get-computation-error"
    st = str(getattr(comp, "state", "")).split(".")[-1].lower()
    if st not in DONE:
        return None, ""            # still running
    exit_code = getattr(comp, "exit_code", None)
    try:
        exit_code = int(exit_code) if exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    end = str(getattr(comp, "end_status", "") or "").split(".")[-1].lower()
    if st == "failed":
        return "failed", "state=failed"
    if exit_code not in (0, None):
        return "failed", f"exit_code={exit_code}"
    if end == "failed":
        return "failed", "end_status=failed"
    return "completed", ""          # provisional — confirm with --verify-capture (asset must exist)


def get_client(domain, token):
    from codeocean import CodeOcean
    domain = domain or os.environ.get("CODEOCEAN_DOMAIN") or crc.DEFAULT_DOMAIN
    if not token:
        token = next((os.environ[v] for v in crc.TOKEN_ENV_VARS if os.environ.get(v)), None)
    if not token:
        sys.exit(f"ERROR: no API token (set one of {crc.TOKEN_ENV_VARS}).")
    return CodeOcean(domain=domain.rstrip("/"), token=token)


def read_items(path, column, include_col, include_val):
    p = pathlib.Path(path)
    out = []
    if p.suffix.lower() != ".csv":
        for line in p.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
    else:
        rows = list(csv.DictReader(open(p)))
        col = column or (rows[0].keys().__iter__().__next__() if rows else None)
        for r in rows:
            if include_col:
                v = (r.get(include_col, "") or "").strip().lower()
                if include_val is not None:
                    if v != include_val.lower():
                        continue
                elif v not in ("1", "true", "yes", "y", "t"):
                    continue
            val = (r.get(col) or "").strip()
            if val:
                out.append(val)
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def resolve(client, item):
    """item -> (asset_id, mount, base_name). Accepts 'id:mount', 'name:mount', 'name'."""
    from codeocean.data_asset import DataAssetSearchParams
    RAW = crc._RAW_SESSION_RE
    spec, mount = (item.split(":", 1) + [None])[:2] if ":" in item else (item, None)
    spec = spec.strip()
    # looks like a bare id?
    if len(spec) == 36 and spec.count("-") == 4:
        name = client.data_assets.get_data_asset(spec).name
        return spec, (mount or name), name
    res = client.data_assets.search_data_assets(DataAssetSearchParams(query=spec, limit=100, archived=False))
    exact = [a for a in getattr(res, "results", []) if a.name == spec]
    raw = [a for a in exact if RAW.match(a.name)] or exact or list(getattr(res, "results", []))
    if not raw:
        raise RuntimeError(f"no data asset for {spec!r}")
    raw.sort(key=lambda a: (getattr(a, "state", "") == "ready", getattr(a, "created", 0)), reverse=True)
    a = raw[0]
    return a.id, (mount or a.name), a.name


def find_existing(client, args, sess_id, base):
    """Return an existing READY result asset that represents THIS run already done, else None.

    Match = name starts with '<base>_<suffix>_' AND provenance.capsule == target capsule
    AND the input session id is in provenance.data_assets. Optional stricter checks:
      --require-data-asset <id> (repeatable): all must be in provenance.data_assets (e.g. model ids)
      --require-commit <sha>: provenance.commit must match (code history)
    """
    from codeocean.data_asset import DataAssetSearchParams
    from codeocean.components import SearchFilter
    suffix = args.process_name_suffix or ""
    prefix = f"{base}_{suffix}_" if suffix else f"{base}_"
    # IMPORTANT: CO's `query`/`name` filters do NOT match a derived capture by its base
    # name (substring/prefix) — they return 0 or only the raw input asset. Only a TAG
    # search reliably returns captured results (verified 2026-07-24). So search by the
    # capture tags (+ subject, which narrows the page set), then filter client-side.
    filters = [SearchFilter(key="tags", value=t) for t in (args.tag or [])]
    parts = str(base).split("_")
    if (len(parts) >= 2 and parts[0].startswith("multiplane-ophys")
            and not getattr(args, "no_subject_tag", False)):
        filters.append(SearchFilter(key="tags", value=parts[1]))   # subject id
    if not filters:
        return None                       # no tag signal to search by (caller treats as 'cannot verify')
    out, offset, limit = [], 0, 100
    while True:
        try:
            res = client.data_assets.search_data_assets(DataAssetSearchParams(
                offset=offset, limit=limit, type="result", archived=False, filters=filters))
        except Exception:
            break
        rs = getattr(res, "results", []) or []
        out.extend(rs)
        if not getattr(res, "has_more", False) or len(out) >= 3000:
            break
        offset += limit
    need_da = set(args.require_data_asset or [])
    for a in out:
        if not str(getattr(a, "name", "")).startswith(prefix):
            continue
        if "ready" not in str(getattr(a, "state", "")).lower():
            continue
        prov = getattr(a, "provenance", None)
        if prov is None:
            continue
        if args.capsule_id and getattr(prov, "capsule", None) != args.capsule_id:
            continue
        pda = set(getattr(prov, "data_assets", []) or [])
        if sess_id not in pda:
            continue
        if need_da and not need_da.issubset(pda):
            continue
        if args.require_commit and getattr(prov, "commit", None) != args.require_commit:
            continue
        return a
    return None


def submit(client, args, item):
    from codeocean.computation import RunParams, DataAssetsRunParam
    asset_id, mount, base = resolve(client, item)
    da = [DataAssetsRunParam(id=asset_id, mount=mount)]
    # extra fixed assets attached to EVERY run (e.g. non-default model checkpoints);
    # each 'id[:mount]' / 'name[:mount]' — mount defaults to the asset name so the
    # capsule's model-dir glob resolves it. (Capsule defaults are still auto-attached.)
    for ea in (getattr(args, "extra_asset", None) or []):
        eid, emount, _ = resolve(client, ea)
        da.append(DataAssetsRunParam(id=eid, mount=emount))
    tags = list(args.tag or [])
    meta = crc.parse_kv(args.meta) if getattr(args, "meta", None) else {}
    # per-item subject tag + "subject id" metadata, derived from a
    # multiplane-ophys_<subject>_<date>_<time> asset name (matches the co_run_capture convention)
    if not getattr(args, "no_subject_tag", False):
        parts = str(base).split("_")
        subj = parts[1] if len(parts) >= 2 and parts[0].startswith("multiplane-ophys") else None
        if subj:
            if subj not in tags:
                tags.append(subj)
            meta.setdefault("subject id", subj)
    capture = {"tags": tags, "process_name_suffix": args.process_name_suffix,
               "custom_metadata": (meta or None)}
    payload = crc.build_monitor_json(args.capsule_id, da, capture, {})
    if len(payload) > crc.MAX_PARAM_LEN:
        raise RuntimeError(f"payload {len(payload)} > {crc.MAX_PARAM_LEN}")
    comp = client.computations.run_capsule(RunParams(capsule_id=args.monitor_capsule_id, parameters=[payload]))
    return comp.id, base, asset_id


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--capsule-id", default=None, help="TARGET capsule id (or use --capsule to look it up)")
    ap.add_argument("--capsule", default=None,
                    help="capsule name or id; looks up capsule-id + suffix + result tags in capsule_registry.json")
    ap.add_argument("--registry", default=None, help="path to capsule_registry.json (default: skill dir)")
    ap.add_argument("--monitor-capsule-id", default=crc.DEFAULT_MONITOR_CAPSULE_ID)
    ap.add_argument("--items-file", required=True, help="txt (one per line) or .csv")
    ap.add_argument("--column", default="session")
    ap.add_argument("--include-col", default=None)
    ap.add_argument("--include-val", default=None)
    ap.add_argument("--process-name-suffix", default=None,
                    help="capture name suffix; if omitted and --capsule is given, taken from the registry "
                         "(else defaults to 'processed')")
    ap.add_argument("--tag", action="append")
    ap.add_argument("--extra-asset", action="append",
                    help="extra fixed data asset attached to every run ('id[:mount]' or 'name[:mount]'); "
                         "e.g. a non-default model checkpoint. Mount defaults to the asset name.")
    ap.add_argument("--meta", action="append",
                    help="custom_metadata key=value (repeatable); applied to every item")
    ap.add_argument("--no-subject-tag", action="store_true",
                    help="disable the auto per-item subject tag + 'subject id' metadata "
                         "derived from the multiplane-ophys_<subject>_... session name")
    ap.add_argument("--skip-existing", action="store_true",
                    help="skip items that already have a READY result from this capsule "
                         "(matched on name <session>_<suffix>_*, provenance.capsule + input session asset). "
                         "Add --require-data-asset / --require-commit for model / code-history strictness.")
    ap.add_argument("--require-data-asset", action="append",
                    help="for --skip-existing: require these asset ids (e.g. the model ids) in the "
                         "existing result's provenance.data_assets (repeatable)")
    ap.add_argument("--require-commit", default=None,
                    help="for --skip-existing: require the existing result's provenance.commit to match")
    ap.add_argument("--max-jobs", type=int, default=10, help="max concurrent monitor jobs (default 10)")
    ap.add_argument("--poll", type=float, default=120, help="poll interval seconds (default 120)")
    ap.add_argument("--state-file", default=None, help="CSV of item,monitor_id,state (resumable)")
    ap.add_argument("--domain", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--retry-failed", action="store_true",
                    help="on resume, re-queue items recorded as failed/unknown/submit-error in the state file")
    ap.add_argument("--max-retries", type=int, default=0,
                    help="in-run: auto-resubmit a failed item up to N more times (default 0)")
    ap.add_argument("--verify-capture", action=argparse.BooleanOptionalAction, default=True,
                    help="after a monitor finishes, confirm a READY result data asset was actually "
                         "captured (provenance match via find_existing) before marking it done; a "
                         "monitor that ran but captured nothing is marked failed (retryable). "
                         "Default on; --no-verify-capture to disable. This is IN ADDITION to the always-on "
                         "exit_code/has_results check (state/end_status alone are unreliable).")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    crc.apply_registry(args)   # --capsule <name|id> -> fill capsule-id + suffix (process_name_suffix) + tags
    if not getattr(args, "process_name_suffix", None):
        args.process_name_suffix = "processed"   # argparse default, restored if registry set None
    if not args.capsule_id:
        sys.exit("ERROR: provide --capsule <name|id> (registry) or --capsule-id <id>.")

    items = read_items(args.items_file, args.column, args.include_col, args.include_val)
    state_file = pathlib.Path(args.state_file or (pathlib.Path(args.items_file).with_suffix(".batchstate.csv")))

    # resume: load prior state (backward-compatible with the old 3-column format)
    RETRYABLE = ("failed", "unknown")
    def is_retryable(st):
        st = st or ""
        return st in RETRYABLE or st.startswith("submit-error")
    STATE_COLS = ["item", "monitor_id", "state", "attempt", "submit_seq", "submit_ts", "done_ts",
                  "exit_code", "has_results", "reason"]
    results = {}   # item -> {monitor_id, state, attempt, submit_seq, submit_ts, done_ts}
    if state_file.exists():
        for r in csv.DictReader(open(state_file)):
            results[r["item"]] = {"monitor_id": r.get("monitor_id", ""), "state": r.get("state", ""),
                                  "attempt": int(r.get("attempt") or 1),
                                  "submit_seq": r.get("submit_seq") or "", "submit_ts": r.get("submit_ts") or "",
                                  "done_ts": r.get("done_ts") or ""}
    if args.retry_failed:
        requeue = [it for it, r in results.items() if is_retryable(r["state"])]
        for it in requeue:
            results.pop(it, None)
        if requeue:
            print(f"retry-failed: re-queuing {len(requeue)} previously failed/unknown item(s)")
    done_before = dict(results)
    todo = [it for it in items if it not in done_before]

    print(f"batch_monitor: {len(items)} items ({len(done_before)} already in state, {len(todo)} to submit)")
    print(f"  target={args.capsule_id}  max_jobs={args.max_jobs}  poll={args.poll}s  "
          f"suffix={args.process_name_suffix}  max_retries={args.max_retries}")

    client = get_client(args.domain, args.token)   # constructs client (no network yet; needed for dedup/resolve)
    inflight = {}   # monitor_id -> item   (from prior run, re-track active ones)
    for it, r in done_before.items():
        if r["state"] in ACTIVE:
            inflight[r["monitor_id"]] = it
    done_order = []   # items in completion order (this run) — for the order-vs-completion report

    def _now():
        return round(time.time(), 1)

    def write_state():
        with open(state_file, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=STATE_COLS); w.writeheader()
            for it, r in results.items():
                row = {"item": it}; row.update({k: r.get(k, "") for k in STATE_COLS[1:]})
                w.writerow(row)

    # ── pre-run dedup: skip items that already have a READY result from this capsule ──
    if args.skip_existing:
        print(f"skip-existing: checking {len(todo)} items against existing results ...", flush=True)
        kept, n_skip = [], 0
        for it in todo:
            try:
                sid, _m, base = resolve(client, it)
            except Exception:
                kept.append(it); continue          # can't resolve -> let submit surface it
            ex = find_existing(client, args, sid, base)
            if ex is not None:
                results[it] = {"monitor_id": ex.id, "state": "skipped-existing", "attempt": 0,
                               "submit_seq": "", "submit_ts": "", "done_ts": ""}; n_skip += 1
                print(f"  [skip-existing] {it}  ->  {ex.id}  {ex.name}", flush=True)
            else:
                kept.append(it)
        write_state()
        print(f"skip-existing: {n_skip} already done (skipped), {len(kept)} to submit", flush=True)
        todo = kept

    if args.dry_run:
        for it in todo:
            print("  [DRY] would submit:", it)
        print(f"[DRY] {len(todo)} submissions; cap {args.max_jobs}. No runs launched.")
        return

    def counts():
        return Counter(r["state"] for r in results.values())

    def verify_captured(it, r):
        """Tri-state check that a READY result asset was actually captured for this item.

        Returns the asset (found), None (searched but NOT found -> real no-capture failure),
        or 'skip' (cannot search -> don't false-fail; rely on exit_code only).
        """
        sid = r.get("sess_id") or it
        base = r.get("base") or ""
        if not base:
            try:
                sid, _m, base = resolve(client, it)
            except Exception:
                return "skip"
        parts = str(base).split("_")
        has_subj = (len(parts) >= 2 and parts[0].startswith("multiplane-ophys")
                    and not getattr(args, "no_subject_tag", False))
        if not (args.tag or has_subj):
            return "skip"                       # no tag signal -> can't verify via search
        try:
            return find_existing(client, args, sid, base)   # asset or None
        except Exception:
            return "skip"

    def poll_inflight():
        """Refresh in-flight monitor states; record TRUE completion + auto-retry failures.

        Trusts exit_code/has_results (+ optional capture verification), NOT `.state`/`.end_status`
        — the monitor reports completed/succeeded even when its target run failed (see classify_outcome).
        """
        for mid, it in list(inflight.items()):
            try:
                comp = client.computations.get_computation(mid)
            except Exception:
                comp = None
            outcome, reason = classify_outcome(comp)
            r = results.get(it, {}); r["monitor_id"] = mid; results[it] = r
            if outcome is None:                 # still running
                r["state"] = str(getattr(comp, "state", "")).split(".")[-1].lower() or "running"
                continue
            # terminal — record the reliable diagnostics
            r["exit_code"] = getattr(comp, "exit_code", "") if comp is not None else ""
            r["has_results"] = bool(getattr(comp, "has_results", False)) if comp is not None else False
            if outcome == "completed" and args.verify_capture:
                v = verify_captured(it, r)
                if v is None:
                    outcome, reason = "failed", "no-capture-asset"   # searched, nothing landed
                elif v == "skip":
                    reason = "capture-unverified"                    # keep completed, but flag it
            r["state"] = outcome; r["reason"] = reason; r["done_ts"] = _now(); done_order.append(it)
            inflight.pop(mid, None)
            if outcome == "completed":
                print(f"  [done] {it}  ({mid})", flush=True)
            else:
                print(f"  [FAILED] {it}  ({mid})  reason={reason}", flush=True)
                if r.get("attempt", 1) <= args.max_retries:
                    r["state"] = f"retry-queued ({reason})"; queue.append(it)
                    print(f"  [auto-retry {r.get('attempt', 1)}/{args.max_retries}] {it}", flush=True)
        write_state()
        return len(inflight)

    queue = list(todo)
    submitted = 0
    while queue or inflight:
        while queue and len(inflight) < args.max_jobs:
            it = queue.pop(0)
            attempt = (results.get(it, {}).get("attempt") or 0) + 1
            try:
                mid, base, sess_id = submit(client, args, it)
                inflight[mid] = it; submitted += 1
                results[it] = {"monitor_id": mid, "state": "initializing", "attempt": attempt,
                               "submit_seq": submitted, "submit_ts": _now(), "done_ts": "",
                               "base": base, "sess_id": sess_id}
                print(f"  [submit #{submitted}] {it}  -> {mid}  (attempt {attempt})")
            except Exception as e:
                results[it] = {"monitor_id": "", "state": f"submit-error: {str(e)[:60]}",
                               "attempt": attempt, "submit_seq": "", "submit_ts": _now(), "done_ts": _now()}
                print(f"  [FAIL submit] {it}: {str(e)[:120]}")
            write_state()
        c = counts(); active = len(inflight); fin = sum(v for k, v in c.items() if k in DONE)
        print(f"  status: submitted={submitted} active={active} finished={fin} "
              f"(completed={c.get('completed',0)} failed={c.get('failed',0)}) queued={len(queue)}", flush=True)
        if not queue and not inflight:
            break
        time.sleep(args.poll)
        poll_inflight()

    # ── final report: submission order vs completion + failures to rerun ──
    c = counts()
    failed = [it for it, r in results.items()
              if r["state"] == "failed" or str(r["state"]).startswith("submit-error")]
    print(f"\nDONE. total={len(results)}  completed={c.get('completed',0)}  failed={len(failed)}  "
          f"skipped-existing={c.get('skipped-existing',0)}")
    print(f"job order vs completion: submit order (submit_seq/submit_ts) + completion (done_ts) recorded "
          f"per item in {state_file.name}; {len(done_order)} completed this run.")
    unverified = [it for it, r in results.items() if r.get("reason") == "capture-unverified"]
    if unverified:
        print(f"\nNOTE: {len(unverified)} item(s) completed but capture could NOT be verified "
              f"(no tag/subject to search by) — pass tags or run search manually to confirm.")
    if failed:
        print(f"\nFAILED ({len(failed)}) — rerun the SAME command with --retry-failed:")
        for it in failed[:30]:
            r = results.get(it, {})
            print(f"   {it}   ({r.get('reason') or r.get('state')})")
        if len(failed) > 30:
            print(f"   ... and {len(failed) - 30} more")
    print(f"\nstate -> {state_file}")


if __name__ == "__main__":
    main()
