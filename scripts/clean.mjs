// Removes generated build artifacts and tool caches only.
// Never touches data/, logs/, config/local.toml, node_modules or the Python virtualenv.
import { readdir, rm } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const targets = [
  "apps/desktop/dist",
  "apps/desktop/node_modules/.tmp",
  "apps/desktop/node_modules/.vite",
  "src-tauri/target",
  "src-tauri/gen/schemas",
  "services/research-engine/.pytest_cache",
  "services/research-engine/.ruff_cache",
  "services/research-engine/.mypy_cache",
];

const engine = join(root, "services/research-engine");
for (const entry of await readdir(engine, { recursive: true, withFileTypes: true })) {
  const dir = join(entry.parentPath, entry.name);
  if (entry.isDirectory() && entry.name === "__pycache__" && !dir.includes(".venv")) {
    targets.push(relative(root, dir));
  }
}

for (const target of targets) {
  await rm(join(root, target), { recursive: true, force: true });
  console.log(`removed ${target}`);
}
