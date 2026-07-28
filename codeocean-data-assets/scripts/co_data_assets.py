#!/usr/bin/env python3
"""codeocean-data-assets — search / attach / detach Code Ocean data assets.

Separate from the codeocean-run-capture skill (which runs capsules + captures
results). attach/detach operate on the CURRENT computation (this session) via
`computations.attach_data_assets` / `detach_data_assets`, so attached assets mount
into /data IMMEDIATELY (an S3 symlink) — no restart. Search + attach mirror
lamf_analysis/code_ocean/code_ocean_utils.py (SearchFilter by tags/name, query,
pagination, DataAssetAttachParams); detach is added here.

Auth   : --token / $CODEOCEAN_TOKEN / $API_SECRET / $CO_TOKEN / $CUSTOM_KEY
Domain : --domain / $CODEOCEAN_DOMAIN / default allenneuraldynamics
Compute: --computation-id / $CO_COMPUTATION_ID   (needed for attach/detach)

  search  --query STR | --tag T (repeat) | --name STR | --subject ID  [--type dataset|result] [--contains STR]
  attach  (--asset ID (repeat) | + any search selector)   [--mount NAME (single)] [--dry-run]
  detach  (--asset ID (repeat) | + any search selector)   [--dry-run]

Examples:
  co_data_assets.py search --tag lp-eye --subject 782149 --type result
  co_data_assets.py attach --tag lp-eye --subject 782149 --type result    # mount all into this session's /data
  co_data_assets.py detach --tag lp-eye --subject 782149 --type result
"""
import argparse, os, sys

DEFAULT_DOMAIN = "https://codeocean.allenneuraldynamics.org"
TOKEN_ENV_VARS = ("CODEOCEAN_TOKEN", "API_SECRET", "CO_TOKEN", "CUSTOM_KEY")


def get_client(args):
    from codeocean import CodeOcean
    domain = getattr(args, "domain", None) or os.environ.get("CODEOCEAN_DOMAIN") or DEFAULT_DOMAIN
    token = getattr(args, "token", None) or next((os.environ[v] for v in TOKEN_ENV_VARS if os.environ.get(v)), None)
    if not token:
        sys.exit(f"ERROR: no API token (set one of {TOKEN_ENV_VARS} or pass --token).")
    return CodeOcean(domain=domain.rstrip("/"), token=token)


def _computation_id(args):
    cid = getattr(args, "computation_id", None) or os.environ.get("CO_COMPUTATION_ID")
    if not cid:
        sys.exit("ERROR: no computation id (set $CO_COMPUTATION_ID or pass --computation-id).")
    return cid


def do_search(client, args):
    """Return a list of matching data assets (paginated). query OR SearchFilters."""
    from codeocean.data_asset import DataAssetSearchParams
    from codeocean.components import SearchFilter
    filters = []
    if args.subject:
        filters.append(SearchFilter(key="tags", value=str(args.subject)))
    if args.name:
        filters.append(SearchFilter(key="name", value=args.name))
    for t in (args.tag or []):
        filters.append(SearchFilter(key="tags", value=t))
    typ = None if (args.type in (None, "all")) else args.type
    out, offset, limit = [], 0, (args.limit or 100)
    while True:
        params = DataAssetSearchParams(offset=offset, limit=limit, sort_order="desc", sort_field="name",
                                       archived=bool(args.archived), favorite=False,
                                       type=typ, query=(args.query or None), filters=(filters or None))
        res = client.data_assets.search_data_assets(params)
        out.extend(res.results)
        if not getattr(res, "has_more", False) or len(out) >= (args.max or 100000):
            break
        offset += limit
    if args.contains:
        out = [a for a in out if args.contains.lower() in (getattr(a, "name", "") or "").lower()]
    return out[: (args.max or len(out))]


def _selected_ids(client, args):
    """Ordered dict {asset_id: name} from explicit --asset ids + any search selector."""
    ids = {}
    for aid in (getattr(args, "asset", None) or []):
        try:
            ids[aid] = client.data_assets.get_data_asset(aid).name
        except Exception:
            ids[aid] = aid
    if args.query or args.tag or args.subject or args.name:
        for a in do_search(client, args):
            ids[a.id] = a.name
    return ids


def cmd_search(args):
    client = get_client(args)
    res = do_search(client, args)
    print(f"{len(res)} data asset(s):")
    for a in res:
        st = str(getattr(a, "state", ""))
        tags = ",".join(getattr(a, "tags", []) or [])
        print(f"  {a.id}  {st:8s}  {a.name}   [{tags}]")


def cmd_attach(args):
    from codeocean.data_asset import DataAssetAttachParams
    client = get_client(args); comp = _computation_id(args)
    ids = _selected_ids(client, args)
    if not ids:
        sys.exit("ERROR: no assets selected (use --asset and/or --name/--tag/--query/--subject).")
    print(f"attach {len(ids)} asset(s) -> computation {comp}:")
    for aid, nm in ids.items():
        print(f"  {aid}  {nm}")
    if args.dry_run:
        print("[DRY] nothing attached."); return
    single = len(ids) == 1
    params = [DataAssetAttachParams(id=aid, mount=(args.mount if (single and args.mount) else None)) for aid in ids]
    results = client.computations.attach_data_assets(computation_id=comp, attach_params=params)
    for r in results:
        print(f"  attached {getattr(r,'id','?')} - mount_state={getattr(r,'mount_state','?')}")
    print("done (mounts appear under /data now — S3 symlink, no restart).")


def cmd_detach(args):
    client = get_client(args); comp = _computation_id(args)
    ids = _selected_ids(client, args)
    if not ids:
        sys.exit("ERROR: no assets selected (use --asset and/or --name/--tag/--query/--subject).")
    print(f"detach {len(ids)} asset(s) <- computation {comp}:")
    for aid, nm in ids.items():
        print(f"  {aid}  {nm}")
    if args.dry_run:
        print("[DRY] nothing detached."); return
    client.computations.detach_data_assets(computation_id=comp, data_assets=list(ids.keys()))
    print("detached.")


def _add_common(p, with_asset=False):
    p.add_argument("--domain", default=None); p.add_argument("--token", default=None)
    p.add_argument("--query", default=None, help="query string (name prefix / full-text)")
    p.add_argument("--tag", action="append", help="SearchFilter tag (repeatable)")
    p.add_argument("--name", default=None, help="SearchFilter name (substring)")
    p.add_argument("--subject", default=None, help="subject id (matched as a tag)")
    p.add_argument("--type", default="all", choices=["all", "dataset", "result"])
    p.add_argument("--contains", default=None, help="client-side name-substring filter")
    p.add_argument("--limit", type=int, default=100, help="page size (default 100)")
    p.add_argument("--max", type=int, default=None, help="cap total results")
    p.add_argument("--archived", action="store_true")
    if with_asset:
        p.add_argument("--asset", action="append", help="explicit asset id (repeatable)")
        p.add_argument("--computation-id", default=None, help="default $CO_COMPUTATION_ID")
        p.add_argument("--dry-run", action="store_true")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="search data assets"); _add_common(s); s.set_defaults(func=cmd_search)
    a = sub.add_parser("attach", help="attach assets to the current computation (immediate mount)")
    _add_common(a, with_asset=True); a.add_argument("--mount", default=None, help="mount name (single asset)")
    a.set_defaults(func=cmd_attach)
    d = sub.add_parser("detach", help="detach assets from the current computation")
    _add_common(d, with_asset=True); d.set_defaults(func=cmd_detach)
    args = ap.parse_args(); args.func(args)


if __name__ == "__main__":
    main()
