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
skills must be reachable there. Don't clone this repo *as* that directory (then every
local skill you create shows up as an untracked change here). Instead clone it into its
own folder and **symlink** the skills into `.claude/skills/`. Pick one of two layouts:

**A. Shared external clone (recommended on a machine with a persistent volume — one
clone serves every capsule, no nested `.git` inside any capsule):**

```bash
SKILLS=/scratch/claude-code-skills-codeocean                 # persistent, off the root overlay
git clone https://github.com/jkim0731/claude-code-skills-codeocean.git "$SKILLS"   # once per machine
cd <capsule-root>; mkdir -p .claude/skills
for name in codeocean-run-capture codeocean-data-assets codeocean-app-panel; do
    ln -sfn "$SKILLS/$name" ".claude/skills/$name"           # absolute symlink -> shared clone
done
```
Trade-off: the symlinks are absolute/external — valid only where `$SKILLS` exists. In a
fresh environment (e.g. a clean Code Ocean checkout) they dangle until the clone is
recreated, so this is NOT self-contained. Use layout B if the capsule must carry the
skills itself.

**B. Submodule inside the capsule (self-contained / portable — travels with the capsule's git):**

```bash
cd <capsule-root>
git submodule add https://github.com/jkim0731/claude-code-skills-codeocean.git code/claude-code-skills-codeocean
mkdir -p .claude/skills
for name in codeocean-run-capture codeocean-data-assets codeocean-app-panel; do
    ln -sfn "../../code/claude-code-skills-codeocean/$name" ".claude/skills/$name"   # relative symlink
done
```

Then **reload / restart Claude Code** so it re-scans `.claude/skills` (discovery runs at startup).

### Capsule-specific (local) skills
Put them as **real folders directly in `.claude/skills/`** (e.g.
`.claude/skills/my-task-skill/SKILL.md`). They live outside `$REPO`, so this shared
repo's `git status` never sees them and they are never committed here — only the
`codeocean-*` symlinks point back into the repo.

### Updating the shared skills later
```bash
git -C "$SKILLS" pull                                     # layout A (external clone)
# layout B (submodule):  git submodule update --remote code/claude-code-skills-codeocean
```
The symlinks pick up the new content automatically; no re-linking needed.

## Quick reference

```bash
S=/scratch/claude-code-skills-codeocean       # layout A; for B use code/claude-code-skills-codeocean
python $S/codeocean-data-assets/scripts/co_data_assets.py attach --asset <id>
python $S/codeocean-run-capture/scripts/co_run_capture.py run --capsule-id <id> --data-asset <id>[:mount] --wait --capture --result-name <name> --tag <t>
python $S/codeocean-app-panel/scripts/check_app_panel.py <capsule_dir>
```
