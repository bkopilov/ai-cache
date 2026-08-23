---
name: ai-cache
description: >-
  Query and save to the local ai-cache before and after every task.
  Use on every user question, diagnosis, fix, or research task.
  Automatically query the cache first, reuse cached answers, replay
  action recipes, and save new findings after completing work.
---

# ai-cache

## Before working: query first

On **every** user question or task, before researching or writing code:

```bash
ai-cache query "<question>"
```

**Always notify the user** what happened:
- Cache hit → tell the user: `[cache hit] Reusing cached answer (score: X.XX)`
- Cache replay → tell the user: `[cache replay] Running cached recipe...`
- Cache miss → tell the user: `[cache miss] Not cached — researching now...`

Read the JSON output:

- `known: true` + answer is enough → reuse `hits[0].answer`. **Stop.**
- `known: true` + has `actions` → replay the recipe:
  ```bash
  ai-cache replay --exec --yes "<question>"
  ```
  If `escalate: false` → **stop.** The cached recipe handled it.
- `known: false` or `escalate: true` → cache cannot help. Proceed with full AI work.

## After working: save automatically

**After every non-trivial response** (diagnosis, fix, research, multi-step answer), save:

```bash
ai-cache save -q "<concise question>" -a "<short answer>"
```

If the work involved shell commands, save them as a replayable recipe:

```bash
ai-cache save -q "<question>" -a "<short answer>" --actions-file - <<'EOF'
[
  {"type": "shell", "summary": "<what step 1 does>", "command": "<command 1>"},
  {"type": "shell", "summary": "<what step 2 does>", "command": "<command 2>"}
]
EOF
```

### Placeholders

Use `{placeholders}` instead of hardcoded hostnames, paths, or usernames so
the recipe works on different machines:

```bash
"command": "ssh {user}@{host} 'systemctl restart {service}'"
```

When saving, **notify the user**: `[cached] Saved answer for: "<question>"`

### Skip saving when

- The response was a greeting, clarification, or simple yes/no
- The answer is already cached (you got it from `ai-cache query`)
- The work was trivial (single obvious command, no diagnosis)

## During troubleshooting: cache continuously

When diagnosing and fixing a problem:

1. Query cache first — maybe it's a known issue
2. If not cached, work on it normally
3. **Save after each meaningful step** (diagnosis, fix attempt, verification) so progress survives if the session is interrupted
4. The **final save** must contain the complete working recipe
5. `save` deduplicates — repeated saves to the same question update the entry

## Managing the cache

```bash
ai-cache list                           # show all entries
ai-cache stats                          # cache location and count
ai-cache delete -q "..." --yes          # remove one entry by question
ai-cache delete --id UUID               # remove one entry by ID
ai-cache delete --all --yes             # wipe everything
ai-cache export -o recipes.json         # share recipes
ai-cache import recipes.json            # load recipes from another machine
```

## Replay with variables

Replay a cached recipe on a different machine without re-asking the AI:

```bash
ai-cache replay --var host=lab-2 --var user=core "Deploy the app"
ai-cache replay --exec --yes --var host=lab-2 "Deploy the app"
```

## Setup

Install: `uv tool install ai-cache` or clone and run `./install.sh`.

Per-project config: create `.ai-cache.json` at project root:
```json
{"store": ".artifacts/ai-cache/"}
```

Without it, the global store at `~/.local/share/ai-cache/` is used.

Verify: `ai-cache doctor` should report `"ok": true`.
