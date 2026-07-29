---
name: codeocean-git-sync
description: Sync a Code Ocean capsule/pipeline with its linked external Git repository (GitHub/GitLab/Bitbucket/Azure DevOps) via POST /api/v1/capsules/{id}/sync — pushes the capsule's commits out AND pulls remote commits in. Use when asked to sync/pull/push a capsule with GitHub, to reconcile a capsule with its remote after editing files in the capsule or on GitHub, or to programmatically trigger the "Sync with the repo" the CO UI does. Requires the capsule be linked to a Git provider and the workspace be unlocked.
---

# codeocean-git-sync

Trigger Code Ocean's **git sync** for a capsule from the API instead of the UI:

```
POST /api/v1/capsules/{capsule_id}/sync   ->   {"pushed": int, "pulled": int, "new_branch": bool}
```

It is **bidirectional** — one call both **pushes** the capsule's local commits to the
linked external repo and **pulls** the remote's commits into the capsule. (Code Ocean
also auto-pulls external pushes on its own; this triggers the reconcile on demand and
also flushes capsule-side commits out.)

The `codeocean` Python client exposes no `sync` method, so the tool issues the raw
request through the client's authenticated session.

## Use it

```bash
S=<skills-dir>/codeocean-git-sync/scripts/co_git_sync.py
python $S                    # sync the CURRENT capsule ($CO_CAPSULE_ID)
python $S <capsule_id>       # sync a specific capsule
python $S --dry-run          # show the POST it would send, without calling
python $S --json             # {"capsule_id":…, "pushed":…, "pulled":…, "new_branch":…}
```

- Auth: `--token` / `$CODEOCEAN_TOKEN` / `$API_SECRET` / `$CO_TOKEN` / `$CUSTOM_KEY`.
- Domain: `--domain` / `$CODEOCEAN_DOMAIN` / default `https://codeocean.allenneuraldynamics.org`.
- Capsule: positional arg, else `$CO_CAPSULE_ID` (the capsule this session runs in).

## Prerequisites (from the API)
- The capsule is **linked to an external Git repo** (set up via *Clone via Git* / Git Provider Integration).
- The caller's **Git credentials are configured** in Code Ocean.
- The **workspace is not locked** — no running/queued computation. Sync fails (HTTP 400/409) otherwise.

## Guardrails
- Syncing is **outward and two-way**: it can push commits to the linked GitHub repo and
  pull commits into the capsule. Confirm the capsule id before running on anything other
  than the current capsule, and be aware there is no pull-only / push-only mode in this endpoint.
- Related skills: **codeocean-run-capture** (run + capture), **codeocean-data-assets**
  (attach/detach assets), **codeocean-app-panel** (validate the App Panel).
