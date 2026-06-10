#!/bin/sh
set -eu

jq -e 'type == "object"' "$GOBLIN_INPUT_PATH" >/dev/null
jq -e 'type == "object"' "$GOBLIN_CONTEXT_PATH" >/dev/null
reason="$(jq -r '.reason // "controlled failure requested"' "$GOBLIN_INPUT_PATH")"
printf 'Shell failure goblin is failing as requested: %s\n' "$reason" >&2

jq -n \
  --arg reason "$reason" \
  --arg run_id "${GOBLIN_RUN_ID:-unknown-run}" \
  '{
    status: "failed",
    data: {
      message: "Controlled failure",
      language: "shell",
      run_id: $run_id
    },
    artifacts: [],
    metrics: {},
    handoff: [],
    error: $reason
  }' > "$GOBLIN_RESULT_PATH"

exit 2
