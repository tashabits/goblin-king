import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const input = JSON.parse(await readFile(process.env.GOBLIN_INPUT_PATH, "utf8"));
const context = JSON.parse(await readFile(process.env.GOBLIN_CONTEXT_PATH, "utf8"));
const artifactRoot = process.env.GOBLIN_ARTIFACT_ROOT;
await mkdir(artifactRoot, { recursive: true });

const name = input.name ?? "goblin-artifact";
const artifactPath = join(artifactRoot, "node-artifact.txt");
await writeFile(artifactPath, `Hello ${name}. Artifact delivered by Node.\\n`);
console.log(`Node artifact goblin wrote ${artifactPath}. The archive scribe nods.`);

const result = {
  status: "success",
  data: {
    message: "Artifact produced",
    language: "node",
    run_id: context.run_id,
    artifact_name: "node-artifact.txt",
  },
  artifacts: [
    {
      name: "node-artifact.txt",
      uri: "artifact://node-artifact.txt",
      media_type: "text/plain",
    },
  ],
  metrics: { artifact_count: 1 },
  handoff: [],
  error: null,
};

await writeFile(process.env.GOBLIN_RESULT_PATH, JSON.stringify(result, null, 2));
