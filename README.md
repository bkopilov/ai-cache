# ai-cache

Local vector cache of prior Q&A so AI agents can skip already-known answers
and replay action recipes without spending LLM tokens.

## Install

Prerequisites: [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
# From the ai-cache repo (editable install):
./install.sh

# Or install directly with uv:
uv tool install ai-cache
```

This puts `ai-cache` on PATH via `uv tool install` (usually `~/.local/bin`)
and downloads the local MiniLM embedding model once. If the command is not
found after install:

```bash
uv tool update-shell                 # writes PATH into your shell rc
# or: export PATH="$(uv tool dir --bin):$PATH"
```

Check:

```bash
ai-cache doctor
ai-cache --version
```

Uninstall: `uv tool uninstall ai-cache`.

## Project setup

Create a `.ai-cache.json` file in your project root to configure the store
location:

```json
{ "store": ".artifacts/ai-cache/" }
```

The `store` path is relative to the directory containing `.ai-cache.json`.

### Store detection priority

1. `AI_CACHE_DIR` environment variable (absolute path)
2. Walk up from cwd looking for `.ai-cache.json` config file
3. Fall back to `~/.local/share/ai-cache/` (XDG-like default)

## Usage

```bash
ai-cache query "How does NAT gateway SNAT work?"

ai-cache save \
  -q "How does NAT gateway SNAT work?" \
  -a "NatGateway is a child of VirtualNetwork. It SNATs egress through an ExternalIP..." \
  --source chat --tags networking

ai-cache save -q "Sprint summary" --answer-file notes.md

# Cache the steps the agent ran (recipe):
ai-cache save -q "Fix BGP session down" -a "Reset FRR neighbor and reapplied the filter" \
  --actions-file - <<'EOF'
[
  {"type": "shell", "summary": "Check BGP neighbors", "command": "vtysh -c 'show ip bgp summary'", "outcome": "ok"},
  {"type": "edit", "summary": "Correct prefix list", "path": "roles/bgp/templates/frr.conf.j2", "outcome": "ok"},
  {"type": "shell", "summary": "Reload FRR", "command": "systemctl reload frr", "outcome": "ok"}
]
EOF

ai-cache list
ai-cache stats
```

## Repeat a previous task without the model

Save the **flow** (commands with `{placeholders}` for host/user/script).
Next time, replay with `--var` for that machine:

```bash
# 1) After a successful connect/install — save the flow, not one hostname:
ai-cache save -q "Connect to my server and run install" \
  -a "SSH then ran the install script" \
  --actions-file - <<'EOF'
[
  {"type": "shell", "summary": "SSH and install", "command": "ssh {user}@{host} '{install}'", "outcome": "ok"}
]
EOF

# 2) Print the recipe for this host (safe):
ai-cache replay --var host=lab-1 --var user=core --var install=./install.sh \
  "Connect to my server and run install"

# 3) Run it (after you confirm the resolved commands look right):
ai-cache replay --exec --yes --var host=lab-1 --var user=core --var install=./install.sh \
  "Connect to my server and run install"
```

## Same flow, different machines

Save a **template**, not one hostname. Use `{placeholders}` in commands:

```bash
ai-cache save -q "Connect to my server and run install" \
  -a "SSH then ran the install script" \
  --actions-file - <<'EOF'
[
  {"type": "shell", "summary": "SSH and install", "command": "ssh {user}@{host} '{install}'", "outcome": "ok"}
]
EOF
```

Replay on lab-1 vs lab-2 without asking the model again:

```bash
ai-cache replay --var host=lab-1 --var user=core --var install=./install.sh \
  "Connect to my server and run install"

ai-cache replay --exec --yes --var host=lab-2 --var user=core --var install=./install.sh \
  "Connect to my server and run install"
```

Optional defaults in `vars.json` (inside the cache dir):

```json
{ "user": "core", "install": "./install.sh" }
```

Copy the flows to another laptop (install `ai-cache` there first):

```bash
ai-cache export -o flows.json     # on machine A
ai-cache import flows.json        # on machine B
```

## Delete an entry

Remove cached entries by ID, question match, or wipe everything:

```bash
# By ID (from ai-cache list output):
ai-cache delete --id 3f2a1b4c-...

# By question (semantic match — shows what it found, requires --yes):
ai-cache delete -q "Connect to my server and run install"
# → shows the match; re-run with --yes to confirm:
ai-cache delete -q "Connect to my server and run install" --yes

# Delete all entries:
ai-cache delete --all         # shows count, asks for --yes
ai-cache delete --all --yes   # wipes everything
```

## Dedup on save

`save` deduplicates automatically. Before creating a new entry, it queries
the cache for semantically similar questions (threshold >= 0.55). If a match
exists, the existing entry is **updated in-place**. The output includes
`"updated": true` and `"previous_question"` when this happens.

To bypass dedup and force a new entry:

```bash
ai-cache save --force -q "..." -a "..."
```

## Auto-save on replay

After a successful `replay --exec --yes` (all shell steps exit 0), the entry's
`last_replayed` timestamp is updated automatically.

## Agent caching mode

The CLI is not the agent. **Caching mode** means: run from cache first;
connect the AI agent for troubleshooting **only if** replay or the live check
fails. Do not auto-escalate on success.

1. `ai-cache query "<question>"` — local vector search, no model.
2. If `known` and you only need the last conclusion, reuse `hits[0].answer`. Stop.
3. If you need **live** and the hit has `actions`:
   `ai-cache replay --exec --yes "<question>"`.
4. If replay succeeds (`replayed` true, `escalate` false) — **stop**.

### If replay fails

JSON includes `"escalate": true` and a hint. Unknown queries also escalate.

**Then** the AI agent troubleshoots. While diagnosing, the agent **caches
continuously** — `ai-cache save` after each meaningful step so progress is
never lost. `save` deduplicates, so repeated saves to the same question update
the existing entry.

The **final save** must contain the complete working recipe (question + answer
+ full ordered `actions`, placeholders, no passwords).

---

`query` prints JSON. `known` is true when the top hit score is >= 0.55
(cosine, MiniLM). Use `replay --exec --yes` when you want the same actions
run again.

Each action may include `type`, `summary`, `command`, `path`, `outcome`,
`notes`. Tokens/passwords in `command`/`notes` are redacted on save.

Override the store with `AI_CACHE_DIR`.
