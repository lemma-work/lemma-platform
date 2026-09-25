// Bake the in-app education concept registry into the installer splash.
// Single source of truth: lemma-frontend/src/site/education/concepts.ts feeds
// the docs, and (via this script) desktop/ui/concepts.gen.json -- from the
// frontend Desktop serves, so the splash and the workspace behind it describe
// the same product.
//
// Usage: node desktop/scripts/extract-concepts.mjs

import fs from "node:fs";
import path from "node:path";
import { spawnSync } from "node:child_process";
import os from "node:os";

const repoRoot = path.resolve(import.meta.dirname, "..", "..");
const sourcePath = path.join(
  repoRoot,
  "lemma-frontend/src/site/education/concepts.ts",
);
const outPath = path.join(repoRoot, "desktop/ui/concepts.gen.json");

// Teaching order for the splash tour. Keep short: the install is a tour, not
// the full curriculum.
const TOUR_ORDER = ["pod", "agent", "flow", "table", "surface", "kit"];

const source = fs.readFileSync(sourcePath, "utf8");
// The only runtime-relevant statement is the CONCEPTS const; imports are
// type-only. Drop import lines, keep the rest, let node strip the types.
const stripped = source
  .split("\n")
  .filter((line) => !line.startsWith("import "))
  .join("\n");

const tmp = path.join(os.tmpdir(), `lemma-concepts-${process.pid}.ts`);
fs.writeFileSync(
  tmp,
  `type ProductIconTone = string;\n${stripped}\nconsole.log(JSON.stringify(CONCEPTS));\n`,
);

const result = spawnSync(
  process.execPath,
  ["--experimental-strip-types", "--no-warnings", tmp],
  { encoding: "utf8" },
);
fs.unlinkSync(tmp);
if (result.status !== 0) {
  console.error(result.stderr);
  process.exit(1);
}

const concepts = JSON.parse(result.stdout.trim());
const tour = TOUR_ORDER.map((id) => {
  const entry = concepts[id];
  if (!entry) throw new Error(`concept ${id} missing from registry`);
  return {
    id: entry.id,
    term: entry.term,
    oneLiner: entry.oneLiner,
    explainer: entry.explainer,
    example: entry.example,
  };
});

fs.writeFileSync(outPath, JSON.stringify({ tour }, null, 2) + "\n");
console.log(`wrote ${outPath} (${tour.length} tour cards)`);
