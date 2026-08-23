#!/usr/bin/env bash
# Install ai-cache onto this machine (uv tool -> ~/.local/bin/ai-cache).
# Usage: ./install.sh
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "ai-cache requires uv." >&2
  echo "Install uv, then re-run this script:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

echo "Installing ai-cache (Python 3.12) with uv tool..."
uv tool install --force --python 3.12 --editable "${DIR}"

TOOL_BIN="$(uv tool dir --bin)"
if ! command -v ai-cache >/dev/null 2>&1; then
  echo ""
  echo "ai-cache is not on PATH yet. Either:"
  echo "  uv tool update-shell          # appends ${TOOL_BIN} to your shell rc"
  echo "or add this to your shell rc and open a new terminal:"
  echo "  export PATH=\"${TOOL_BIN}:\$PATH\""
  echo ""
  AICACHE="${TOOL_BIN}/ai-cache"
else
  AICACHE="ai-cache"
fi

echo "Warming the local embedding model (one-time download if missing)..."
export ANONYMIZED_TELEMETRY="${ANONYMIZED_TELEMETRY:-False}"
export HF_HUB_DISABLE_PROGRESS_BARS="${HF_HUB_DISABLE_PROGRESS_BARS:-1}"
"${AICACHE}" warmup

echo ""
echo "Setting up global AI agent rules..."

CACHE_RULE='On **every** user question or task, run `ai-cache query "..."` before researching (costs 0 LLM tokens). Then:

1. `known` + answer is enough → reuse `hits[0].answer`. **Stop.**
2. `known` + has `actions` → `ai-cache replay --exec --yes "..."`. If `escalate: false` → **stop.**
3. `escalate: true` (unknown / replay fail / live disagrees) → AI takes over. **Cache continuously**: `ai-cache save` after each meaningful step so progress is never lost.

`save` deduplicates automatically. Install: `uv tool install ai-cache`. Verify: `ai-cache doctor`.
Per-project store: create `.ai-cache.json` with `{"store": ".artifacts/ai-cache/"}`. Without it, `~/.local/share/ai-cache/` is used.'

# Claude Code global rule
CLAUDE_DIR="${HOME}/.claude"
if [ -d "${CLAUDE_DIR}" ] || command -v claude >/dev/null 2>&1; then
  mkdir -p "${CLAUDE_DIR}"
  SAVE_RULE='## After answering: save automatically

**After every non-trivial response**, save what you learned:

```bash
ai-cache save -q "<concise question>" -a "<short answer>"
```

If the work involved shell commands, include `--actions-file` with the steps.'

  if [ ! -f "${CLAUDE_DIR}/CLAUDE.md" ]; then
    printf "# Global rules\n\n## ai-cache — automatic caching\n\n### Before answering: query first\n\n%s\n\n### %s\n" "${CACHE_RULE}" "${SAVE_RULE}" \
      > "${CLAUDE_DIR}/CLAUDE.md"
    echo "  Created ${CLAUDE_DIR}/CLAUDE.md"
  elif ! grep -q "ai-cache" "${CLAUDE_DIR}/CLAUDE.md" 2>/dev/null; then
    printf "\n## ai-cache — automatic caching\n\n### Before answering: query first\n\n%s\n\n### %s\n" "${CACHE_RULE}" "${SAVE_RULE}" \
      >> "${CLAUDE_DIR}/CLAUDE.md"
    echo "  Appended ai-cache rule to ${CLAUDE_DIR}/CLAUDE.md"
  else
    echo "  ${CLAUDE_DIR}/CLAUDE.md already has ai-cache rule"
  fi
fi

# Cursor global rule
CURSOR_DIR="${HOME}/.cursor/rules"
if [ -d "${HOME}/.cursor" ] || command -v cursor >/dev/null 2>&1; then
  mkdir -p "${CURSOR_DIR}"
  if [ ! -f "${CURSOR_DIR}/ai-cache.md" ]; then
    printf "# ai-cache — automatic caching\n\n## Before answering: query first\n\n%s\n\n## After answering: save automatically\n\n**After every non-trivial response**, save what you learned:\n\n\`\`\`bash\nai-cache save -q \"<concise question>\" -a \"<short answer>\"\n\`\`\`\n\nIf the work involved shell commands, include \`--actions-file\` with the steps.\n" "${CACHE_RULE}" \
      > "${CURSOR_DIR}/ai-cache.md"
    echo "  Created ${CURSOR_DIR}/ai-cache.md"
  else
    echo "  ${CURSOR_DIR}/ai-cache.md already exists"
  fi

  # Cursor stop hook for automatic caching
  CURSOR_HOOKS_DIR="${HOME}/.cursor/hooks"
  CURSOR_HOOKS_JSON="${HOME}/.cursor/hooks.json"
  mkdir -p "${CURSOR_HOOKS_DIR}"
  if [ ! -f "${CURSOR_HOOKS_DIR}/auto-cache.sh" ]; then
    cat > "${CURSOR_HOOKS_DIR}/auto-cache.sh" <<'HOOK'
#!/bin/bash
if ! command -v ai-cache >/dev/null 2>&1; then exit 0; fi
input=$(cat)
stop_reason=$(echo "$input" | jq -r '.stopReason // "end_turn"')
if [[ "$stop_reason" != "end_turn" && "$stop_reason" != "stop" ]]; then exit 0; fi
echo '{"followup_message": "If you performed any diagnosis, fix, research, or answered a question in this turn that is worth remembering, save it now: ai-cache save -q \"<concise question>\" -a \"<short answer>\". If the work involved shell commands, include --actions-file. If nothing new was learned or it was a trivial response, skip this step."}'
HOOK
    chmod +x "${CURSOR_HOOKS_DIR}/auto-cache.sh"
    echo "  Created ${CURSOR_HOOKS_DIR}/auto-cache.sh"
  fi
  if [ ! -f "${CURSOR_HOOKS_JSON}" ]; then
    cat > "${CURSOR_HOOKS_JSON}" <<'JSON'
{"version":1,"hooks":{"stop":[{"command":"./hooks/auto-cache.sh","loop_limit":1}]}}
JSON
    echo "  Created ${CURSOR_HOOKS_JSON}"
  elif ! grep -q "auto-cache" "${CURSOR_HOOKS_JSON}" 2>/dev/null; then
    echo "  NOTE: ${CURSOR_HOOKS_JSON} exists but does not have the auto-cache hook."
    echo "        Add manually: {\"command\":\"./hooks/auto-cache.sh\",\"loop_limit\":1} to the stop event."
  fi
fi

# Install the ai-cache skill for Cursor and Claude Code
SKILL_SRC="${DIR}/skill"
if [ -d "${SKILL_SRC}" ]; then
  # Cursor skill
  CURSOR_SKILLS="${HOME}/.cursor/skills/ai-cache"
  if [ -d "${HOME}/.cursor" ] || command -v cursor >/dev/null 2>&1; then
    mkdir -p "${CURSOR_SKILLS}"
    cp "${SKILL_SRC}/SKILL.md" "${CURSOR_SKILLS}/SKILL.md"
    echo "  Installed Cursor skill at ${CURSOR_SKILLS}/SKILL.md"
  fi

  # Claude Code skill
  CLAUDE_SKILLS="${HOME}/.claude/skills/ai-cache"
  if [ -d "${HOME}/.claude" ] || command -v claude >/dev/null 2>&1; then
    mkdir -p "${CLAUDE_SKILLS}"
    cp "${SKILL_SRC}/SKILL.md" "${CLAUDE_SKILLS}/SKILL.md"
    echo "  Installed Claude Code skill at ${CLAUDE_SKILLS}/SKILL.md"
  fi
fi

echo ""
echo "Installed. Next:"
echo "  ai-cache doctor"
echo "  ai-cache --version"
