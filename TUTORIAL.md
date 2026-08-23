# ai-cache — Getting Started Tutorial

You just rebooted. You have Cursor or Claude Code installed. This tutorial
gets ai-cache working in under 5 minutes so your AI agent remembers
previous answers and stops repeating work.

## Step 1: Install uv (if you don't have it)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Close and reopen your terminal (or `source ~/.bashrc`).

## Step 2: Install ai-cache

Clone the repo and run the installer:

```bash
git clone https://github.com/bkopilov/ai-cache.git
cd ai-cache
./install.sh
```

This does three things:
- Installs the `ai-cache` CLI to `~/.local/bin/`
- Downloads the local embedding model (~80 MB, one time)
- Creates global rules for Cursor and Claude Code automatically

If `ai-cache` isn't found after install:

```bash
uv tool update-shell    # adds ~/.local/bin to your PATH
# then open a new terminal
```

## Step 3: Verify

```bash
ai-cache doctor
```

You should see `"ok": true`. That means:
- The CLI is installed and on PATH
- The embedding model is downloaded
- The agent rules are in place

## Step 4: Use it — just open your editor

That's it. No extra steps per session.

**Cursor:** Open any project folder. The global rule at
`~/.cursor/rules/ai-cache.md` tells the AI to query the cache first on
every question.

**Claude Code:** Run `claude` in any directory. The global rule at
`~/.claude/CLAUDE.md` does the same thing.

The AI will automatically:
1. Check the cache before researching
2. Reuse cached answers when found
3. Save new answers after solving problems

You don't need to type any special commands or mention ai-cache.

## What happens behind the scenes

```
You: "Why is the BGP session down?"

AI (invisible to you):
  → runs: ai-cache query "Why is the BGP session down?"
  → cache says: known=false, escalate=true
  → AI researches, SSHs, diagnoses, fixes
  → runs: ai-cache save -q "Why is the BGP session down?" -a "..." --actions-file ...
  → gives you the answer

Next time you (or anyone with the same cache) ask a similar question:
  → runs: ai-cache query "BGP session down"
  → cache says: known=true
  → AI reuses the cached answer instantly — no research, no SSH
```

## Optional: Per-project cache

By default, all cached answers go to `~/.local/share/ai-cache/` (shared
across all projects).

To give a project its own separate cache, create `.ai-cache.json` in the
project root:

```bash
echo '{"store": ".artifacts/ai-cache/"}' > /path/to/my-project/.ai-cache.json
```

Add `.artifacts/` to your `.gitignore`. Now that project's answers stay
local to it.

## Try it manually

You can use ai-cache directly from the terminal too (no AI needed):

```bash
# Save something you learned:
ai-cache save -q "How to restart the FRR service" -a "systemctl restart frr"

# Ask later:
ai-cache query "restart FRR"
# → known: true, shows your saved answer

# See everything cached:
ai-cache list

# Delete one entry:
ai-cache delete -q "restart FRR" --yes

# Wipe the entire cache:
ai-cache delete --all --yes
```

## Save a repeatable recipe

When the AI fixes something with multiple steps, save the commands as a
recipe so it can be replayed without AI tokens:

```bash
ai-cache save -q "Deploy app to staging" \
  -a "Build, push, and deploy to staging cluster" \
  --actions-file - <<'EOF'
[
  {"type": "shell", "summary": "Build image", "command": "podman build -t {image} ."},
  {"type": "shell", "summary": "Push image", "command": "podman push {image}"},
  {"type": "shell", "summary": "Deploy", "command": "kubectl apply -k deploy/staging/"}
]
EOF
```

Replay it:

```bash
# Preview (safe, no execution):
ai-cache replay "Deploy app to staging" --var image=myapp:v2

# Execute:
ai-cache replay --exec --yes "Deploy app to staging" --var image=myapp:v2
```

## Share recipes across machines

```bash
# Export from machine A:
ai-cache export -o my-recipes.json

# Import on machine B (ai-cache must be installed there):
ai-cache import my-recipes.json
```

## After a reboot

Nothing to do. `ai-cache` is a binary on disk, cached entries are files
on disk, and the agent rules are files on disk. Open your editor and start
asking questions — the cache is already there.

## Quick reference

| Command | What it does |
|---------|-------------|
| `ai-cache query "..."` | Search the cache (0 tokens) |
| `ai-cache save -q "..." -a "..."` | Save an answer |
| `ai-cache replay "..."` | Preview a cached recipe |
| `ai-cache replay --exec --yes "..."` | Run a cached recipe |
| `ai-cache list` | Show all cached entries |
| `ai-cache stats` | Cache location and count |
| `ai-cache delete --id UUID` | Delete one entry by ID |
| `ai-cache delete -q "..." --yes` | Delete by question match |
| `ai-cache delete --all --yes` | Wipe the entire cache |
| `ai-cache export -o file.json` | Export recipes to share |
| `ai-cache import file.json` | Import recipes |
| `ai-cache doctor` | Check installation health |
| `ai-cache warmup` | Download embedding model |
