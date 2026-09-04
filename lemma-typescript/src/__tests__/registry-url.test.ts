import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import path from "node:path";

// `npx lemma-sdk init-shadcn` writes the URL every documented
// `npx shadcn add @lemma/...` then resolves against. It used to write a
// github.io URL for a Pages site that was never enabled, so the whole
// registry story 404'd. Offline, this asserts the URL is pinned to this
// package's release tag and points at registry files that actually exist in
// the tree the tag is cut from — the half that a dead URL fails.
const repoRoot = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const cli = path.join(repoRoot, "bin", "lemma-sdk.js");
const version = JSON.parse(
  readFileSync(path.join(repoRoot, "package.json"), "utf-8"),
).version as string;

function initShadcnIn(dir: string): { registries: Record<string, string> } {
  execFileSync(process.execPath, [cli, "init-shadcn"], { cwd: dir });
  return JSON.parse(readFileSync(path.join(dir, "components.json"), "utf-8"));
}

describe("shadcn registry URL", () => {
  let dir: string;

  beforeEach(() => {
    dir = mkdtempSync(path.join(tmpdir(), "lemma-sdk-registry-"));
  });

  afterEach(() => {
    rmSync(dir, { recursive: true, force: true });
  });

  it("pins the @lemma namespace to this release's registry files", () => {
    const config = initShadcnIn(dir);
    const url = config.registries["@lemma"];

    expect(url).toBe(
      `https://cdn.jsdelivr.net/gh/lemma-work/lemma-platform@v${version}/lemma-typescript/public/r/{name}.json`,
    );
    expect(url).not.toContain("github.io");
    // A moving branch would silently re-point every previously installed block.
    expect(url).not.toContain("@main");
  });

  it("points at a path that holds the registry it promises", () => {
    const url = initShadcnIn(dir).registries["@lemma"];
    const served = url.replace(
      `https://cdn.jsdelivr.net/gh/lemma-work/lemma-platform@v${version}/lemma-typescript/`,
      "",
    );

    expect(served).toBe("public/r/{name}.json");
    for (const name of ["registry", "lemma-records-view", "lemma-ui"]) {
      expect(
        existsSync(path.join(repoRoot, served.replace("{name}", name))),
      ).toBe(true);
    }
  });

  it("leaves an unrelated components.json otherwise untouched", () => {
    const before = { $schema: "https://ui.shadcn.com/schema.json", aliases: { ui: "@/ui" } };
    execFileSync(process.execPath, ["-e", `require("fs").writeFileSync("components.json", ${JSON.stringify(JSON.stringify(before))})`], { cwd: dir });

    const config = initShadcnIn(dir) as unknown as Record<string, unknown>;

    expect(config.aliases).toEqual({ ui: "@/ui" });
    expect(Object.keys(config.registries as object)).toEqual(["@lemma"]);
  });
});
