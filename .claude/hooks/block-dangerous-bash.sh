#!/usr/bin/env bash
# PreToolUse:Bash hook — denies a small set of obviously destructive commands.
# Input: JSON on stdin with `.tool_input.command`.
# Emits a PreToolUse permissionDecision=deny JSON when a pattern matches; otherwise silent.
# This is a safety net, not a security boundary — users can still override by running the command themselves.

set +e

cmd=$(jq -r '.tool_input.command // empty')
[ -z "$cmd" ] && exit 0

deny() {
  local reason="$1"
  jq -n --arg r "$reason" --arg c "$cmd" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: ($r + " — blocked by project safety hook. Command: " + $c)
    }
  }'
  exit 0
}

# rm -rf against root / home
if echo "$cmd" | grep -qE 'rm[[:space:]]+-[rRfF]{2,}[[:space:]]+(/|~|\$HOME)([[:space:]/]|$)'; then
  deny "rm -rf targeting /, ~, or \$HOME"
fi

# git reset --hard
if echo "$cmd" | grep -qE 'git[[:space:]]+reset[[:space:]]+--hard'; then
  deny "git reset --hard discards uncommitted work"
fi

# git clean -f / -fd / -fdx
if echo "$cmd" | grep -qE 'git[[:space:]]+clean[[:space:]]+-[fFdDxX]+'; then
  deny "git clean -f removes untracked files irreversibly"
fi

# git push --force / -f to main/master
if echo "$cmd" | grep -qE 'git[[:space:]]+push\b' &&
   echo "$cmd" | grep -qE '(--force([[:space:]]|=|$)|[[:space:]]-f([[:space:]]|$))' &&
   echo "$cmd" | grep -qE '([[:space:]:]|^)(main|master)([[:space:]]|$)'; then
  deny "git push --force to main/master"
fi

# git push --force without --force-with-lease anywhere (catches the common footgun)
if echo "$cmd" | grep -qE 'git[[:space:]]+push\b' &&
   echo "$cmd" | grep -qE -e '--force([[:space:]]|=|$)' &&
   ! echo "$cmd" | grep -qE -e '--force-with-lease'; then
  deny "git push --force (use --force-with-lease instead)"
fi

# SQL drops / truncate
if echo "$cmd" | grep -qiE '\b(drop[[:space:]]+(database|schema|table)|truncate[[:space:]]+table)\b'; then
  deny "destructive SQL statement (DROP / TRUNCATE)"
fi

exit 0
