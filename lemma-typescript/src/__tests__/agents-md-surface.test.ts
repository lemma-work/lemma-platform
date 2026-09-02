import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

// AGENTS.md is written for code-generating agents: it is the file an agent
// reads before writing an app, so every name in it lands verbatim in generated
// code. Six hook names in the selection guide did not exist, and each one was a
// build failure in code the user did not write. This keeps the guide and the
// barrel in step.
const sdkDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const agentsMd = readFileSync(path.join(sdkDir, "AGENTS.md"), "utf-8");

/** Hook names AGENTS.md mentions in backticks, in the hook-selection guide. */
function documentedHooks(): string[] {
  const guide = agentsMd.slice(agentsMd.indexOf("## Hook selection guide"));
  const end = guide.indexOf("\n## ", 1);
  const section = end === -1 ? guide : guide.slice(0, end);
  return [...new Set([...section.matchAll(/`(use[A-Z][A-Za-z0-9_]*)`/g)].map((m) => m[1]))];
}

describe("AGENTS.md hook selection guide", () => {
  it("names only hooks lemma-sdk/react actually exports", async () => {
    const barrel = (await import("../react/index.js")) as Record<string, unknown>;

    const missing = documentedHooks().filter((name) => !(name in barrel));

    expect(missing).toEqual([]);
  });

  it("covers enough of the barrel to be worth reading", () => {
    // Guards the other direction: a guide that has quietly stopped tracking the
    // hooks is as misleading as one that invents them.
    expect(documentedHooks().length).toBeGreaterThan(30);
  });

  it("agrees with registry.json on how many blocks ship", () => {
    const registry = JSON.parse(
      readFileSync(path.join(sdkDir, "registry.json"), "utf-8"),
    ) as { items: { name: string }[] };
    // `lemma-ui` is the shared primitive layer, not a block.
    const blocks = registry.items.filter((item) => item.name !== "lemma-ui");

    expect(agentsMd).toContain(`**${blocks.length} canonical blocks**`);
  });
});
