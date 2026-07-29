#!/usr/bin/env python3
"""codeocean-git-sync — sync a Code Ocean capsule/pipeline with its linked external
Git repository (GitHub / GitLab / Bitbucket / Azure DevOps).

Calls the Code Ocean REST endpoint  POST /api/v1/capsules/{capsule_id}/sync , which
is **bidirectional**: it pushes the capsule's local commits out to the remote AND
pulls remote commits in. Response: {"pushed": int, "pulled": int, "new_branch": bool}.

The `codeocean` Python client has no `sync` helper, so this issues the raw request
through the client's authenticated session (base url {domain}/api/v1/).

Auth   : --token / $CODEOCEAN_TOKEN / $API_SECRET / $CO_TOKEN / $CUSTOM_KEY
Domain : --domain / $CODEOCEAN_DOMAIN / default allenneuraldynamics
Capsule: positional CAPSULE_ID, else $CO_CAPSULE_ID (the current capsule)

Prerequisites (per the CO API): the capsule must be linked to an external Git repo
("Clone via Git" set up), the caller's Git credentials must be configured in Code
Ocean, and the capsule workspace must not be locked (no running/queued computation).

Examples:
  co_git_sync.py                 # sync the current capsule ($CO_CAPSULE_ID)
  co_git_sync.py <capsule_id>    # sync a specific capsule
  co_git_sync.py --dry-run       # show the request that would be sent, don't call
  co_git_sync.py --json          # machine-readable result
"""
import argparse
import json
import os
import sys

DEFAULT_DOMAIN = "https://codeocean.allenneuraldynamics.org"
TOKEN_ENV_VARS = ("CODEOCEAN_TOKEN", "API_SECRET", "CO_TOKEN", "CUSTOM_KEY")


def get_client(args):
    from codeocean import CodeOcean
    domain = getattr(args, "domain", None) or os.environ.get("CODEOCEAN_DOMAIN") or DEFAULT_DOMAIN
    token = getattr(args, "token", None) or next((os.environ[v] for v in TOKEN_ENV_VARS if os.environ.get(v)), None)
    if not token:
        sys.exit(f"ERROR: no API token (set one of {TOKEN_ENV_VARS} or pass --token).")
    return CodeOcean(domain=domain.rstrip("/"), token=token), domain.rstrip("/")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capsule_id", nargs="?", default=os.environ.get("CO_CAPSULE_ID"),
                    help="capsule/pipeline id to sync (default: $CO_CAPSULE_ID)")
    ap.add_argument("--domain", default=None)
    ap.add_argument("--token", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the request, don't call the API")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args(argv)

    if not a.capsule_id:
        sys.exit("ERROR: no capsule id (pass CAPSULE_ID or set $CO_CAPSULE_ID).")

    client, domain = get_client(a)
    path = f"capsules/{a.capsule_id}/sync"
    url = f"{domain}/api/v1/{path}"

    if a.dry_run:
        print(f"[dry-run] POST {url}  (bidirectional git sync; no request body)")
        return 0

    try:
        resp = client.session.post(path)        # session base_url = {domain}/api/v1/
        result = resp.json() if resp.content else {}
    except Exception as e:  # noqa: BLE001
        # the client raises requests.HTTPError (via its response hook) on non-2xx
        msg = str(e)
        status = getattr(getattr(e, "response", None), "status_code", None)
        hint = ""
        if status in (400, 409):
            hint = ("  (is the capsule linked to a Git provider, your Git credentials "
                    "configured, and the workspace unlocked — no running computation?)")
        elif status in (401, 403):
            hint = "  (token lacks permission on this capsule, or bad credentials)"
        sys.exit(f"ERROR: git sync failed for capsule {a.capsule_id}"
                 f"{f' (HTTP {status})' if status else ''}: {msg}{hint}")

    if a.json:
        print(json.dumps({"capsule_id": a.capsule_id, **result}, indent=2))
    else:
        pushed = result.get("pushed"); pulled = result.get("pulled"); nb = result.get("new_branch")
        print(f"git sync OK  capsule={a.capsule_id}  pushed={pushed}  pulled={pulled}  new_branch={nb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
