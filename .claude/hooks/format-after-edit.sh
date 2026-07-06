#!/usr/bin/env bash
# PostToolUse:Edit|Write hook — auto-formats the edited file by extension.
# Input: JSON on stdin with `.tool_input.file_path` (or `.tool_response.filePath`).
# Never fails the tool call: all errors are swallowed.

set +e

file_path=$(jq -r '.tool_response.filePath // .tool_input.file_path // empty')
[ -z "$file_path" ] && exit 0
[ ! -f "$file_path" ] && exit 0

cd "$(git rev-parse --show-toplevel 2>/dev/null || dirname "$file_path")" 2>/dev/null || exit 0

case "$file_path" in
  *.py)
    uv run ruff format --quiet "$file_path" >/dev/null 2>&1
    uv run ruff check --fix --quiet "$file_path" >/dev/null 2>&1
    ;;
  *.ts | *.tsx | *.js | *.jsx | *.json | *.jsonc)
    bunx biome check --write --no-errors-on-unmatched "$file_path" >/dev/null 2>&1
    ;;
esac

exit 0
