import { readFile, writeFile } from "node:fs/promises";

async function readObject(path, label) {
  const value = JSON.parse(await readFile(path, "utf8"));
  if (value === null || Array.isArray(value) || typeof value !== "object") {
    throw new Error(`${label} must be a JSON object`);
  }
  return value;
}

const input = await readObject(process.env.GOBLIN_INPUT_PATH, "input");
await readObject(process.env.GOBLIN_CONTEXT_PATH, "context");

const runId = process.env.GOBLIN_RUN_ID ?? "unknown-run";
const kind = process.env.GOBLIN_KIND ?? "example.hello-node";
const target = input.target ?? "World";

console.log(`Node goblin says hello to ${target}. The event loop bows.`);

const result = {
  status: "success",
  data: {
    message: "Hello World",
    language: "node",
    runtime: "Node.js 22",
    kind,
    run_id: runId,
    target,
    input,
    quote: "The royal event loop never blocks the throne room.",
  },
  artifacts: [],
  metrics: {},
  handoff: [],
  error: null,
};

await writeFile(process.env.GOBLIN_RESULT_PATH, JSON.stringify(result, null, 2));
