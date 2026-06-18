import { cpSync, existsSync, mkdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const staticDir = join(root, "static");
const distDir = join(root, "dist");
const required = ["index.html", "app.js", "styles.css"];

for (const file of required) {
  const path = join(staticDir, file);
  if (!existsSync(path) || !statSync(path).isFile()) {
    throw new Error(`missing static asset: ${file}`);
  }
}

rmSync(distDir, { recursive: true, force: true });
mkdirSync(distDir, { recursive: true });
cpSync(staticDir, distDir, { recursive: true });
console.log("directory UI static build complete");
