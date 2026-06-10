#!/bin/sh
set -eu

input_path="${GOBLIN_INPUT_PATH:?GOBLIN_INPUT_PATH is required}"
context_path="${GOBLIN_CONTEXT_PATH:?GOBLIN_CONTEXT_PATH is required}"
result_path="${GOBLIN_RESULT_PATH:?GOBLIN_RESULT_PATH is required}"
run_id="${GOBLIN_RUN_ID:-unknown-run}"
kind="${GOBLIN_KIND:-example.hello-shell}"

jq -e 'type == "object"' "$input_path" >/dev/null
jq -e 'type == "object"' "$context_path" >/dev/null

target="$(jq -r '.target // "World"' "$input_path")"
printf 'Shell goblin says hello to %s. The crown approves of pipes.\n' "$target"

jq -n \
  --arg run_id "$run_id" \
  --arg kind "$kind" \
  --arg target "$target" \
  --slurpfile input "$input_path" \
  '{
    status: "success",
    data: {
      message: "Hello World",
      language: "shell",
      runtime: "POSIX shell with jq",
      kind: $kind,
      run_id: $run_id,
      target: $target,
      input: $input[0],
      quote: "A tiny shell script may still wear a crown."
    },
    artifacts: [],
    metrics: {},
    handoff: [],
    error: null
  }' > "$result_path"
