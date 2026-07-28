# claude-code-skills-codeocean

Shared [Claude Code](https://claude.com/claude-code) **skills** for driving Code Ocean
(AIND deployment) from inside a capsule. Clone this repo into a capsule and **symlink
its skills into `.claude/skills/`** (the directory Claude Code scans) — see the how-to
below. Keeping the repo in its own folder (not *as* `.claude/skills/`) lets a capsule
also have its own task-specific skills without them leaking into this shared repo.

## Skills

| skill | what it does |
|-------|--------------|
| **codeocean-run-capture** | Run a CO capsule/pipeline with attached data assets and capture the results as a named, tagged data asset. Includes per-session / per-subject batch orchestration (`run_per_session.sh`, `run_per_subject.sh`) and a server-side pipeline-monitor mode. |
| **codeocean-data-assets** | Search, **attach** (mount into the current computation's `/data` — immediate, no restart), and **detach** CO data assets by id / tag / name / subject. |
| **codeocean-app-panel** | Author & validate a capsule's App Panel (`.codeocean/app-panel.json`) and keep its parameters in sync with `code/run_capsule.py` argparse (static checker — every panel param's key must map to an argparse `--flag`). |

Each skill is a folder with a `SKILL.md` (the instructions Claude reads) plus a
`scripts/` directory with the underlying Python/bash tools you can also run by hand.

## Prerequisites

- `pip install codeocean`
- API token in the environment (checked in order): `$CODEOCEAN_TOKEN`, `$API_SECRET`,
  `$CO_TOKEN`, `$CUSTOM_KEY` — or pass `--token`.
- Domain from `$CODEOCEAN_DOMAIN` or `--domain` (default: the AIND deployment,
  `https://codeocean.allenneuraldynamics.org`).

## How to add to a capsule

Claude Code discovers **project skills from `<capsule>/.claude/skills/`** — so the
skills must be reachable there. Rather than clone this repo *as* that directory (which
would make every local skill you create show up as an untracked change in this shared
repo), clone it into its own folder and symlink the skills in:

```bash
cd <capsule-root>                        # the dir with code/, .claude/, environment/
REPO=code/claude-code-skills-codeocean

# 1. bring in the shared repo (pick ONE):
git clone https://github.com/jkim0731/claude-code-skills-codeocean.git "$REPO"
#   …or pin it as a submodule (records the commit in the capsule's git):
#   git submodule add https://github.com/jkim0731/claude-code-skills-codeocean.git "$REPO"

# 2. expose each shared skill to Claude Code via a symlink:
mkdir -p .claude/skills
for s in "$REPO"/codeocean-*/; do
    ln -sfn "../../$REPO/$(basename "$s")" ".claude/skills/$(basename "$s")"
done

# 3. reload / restart Claude Code so it re-scans .claude/skills (discovery runs at startup).
```

### Capsule-specific (local) skills
Put them as **real folders directly in `.claude/skills/`** (e.g.
`.claude/skills/my-task-skill/SKILL.md`). They live outside `$REPO`, so this shared
repo's `git status` never sees them and they are never committed here — only the
`codeocean-*` symlinks point back into the repo.

### Updating the shared skills later
```bash
git -C code/claude-code-skills-codeocean pull          # plain clone
# or, if a submodule:  git submodule update --remote code/claude-code-skills-codeocean
```
The symlinks pick up the new content automatically; no re-linking needed.

## Quick reference

```bash
S=code/claude-code-skills-codeocean
python $S/codeocean-data-assets/scripts/co_data_assets.py attach --asset <id>
python $S/codeocean-run-capture/scripts/co_run_capture.py run --capsule-id <id> --data-asset <id>[:mount] --wait --capture --result-name <name> --tag <t>
python $S/codeocean-app-panel/scripts/check_app_panel.py <capsule_dir>
```
