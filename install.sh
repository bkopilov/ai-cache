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
  if [ ! -f "${CLAUDE_DIR}/CLAUDE.md" ]; then
    printf "# Global rules\n\n## ai-cache — query before researching\n\n%s\n" "${CACHE_RULE}" \
      > "${CLAUDE_DIR}/CLAUDE.md"
    echo "  Created ${CLAUDE_DIR}/CLAUDE.md"
  elif ! grep -q "ai-cache" "${CLAUDE_DIR}/CLAUDE.md" 2>/dev/null; then
    printf "\n## ai-cache — query before researching\n\n%s\n" "${CACHE_RULE}" \
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
    printf "# ai-cache — query before researching\n\n%s\n" "${CACHE_RULE}" \
      > "${CURSOR_DIR}/ai-cache.md"
    echo "  Created ${CURSOR_DIR}/ai-cache.md"
  else
    echo "  ${CURSOR_DIR}/ai-cache.md already exists"
  fi
fi

echo ""
echo "Installed. Next:"
echo "  ai-cache doctor"
echo "  ai-cache --version"
