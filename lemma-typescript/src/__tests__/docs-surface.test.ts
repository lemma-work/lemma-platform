import { describe, it, expect } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { LemmaClient } from "../client.js";

// README.md is the first file a stranger reads and the file an LLM is most
// likely to have been trained on, so a stale name in it is the name that lands
// in code nobody wrote by hand. Three namespaces here outlived a rename
// (`desks`, `integrations`, `resources`) and each one was `undefined` at
// runtime. These tests keep the prose and the client in step.
const sdkDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const readme = readFileSync(path.join(sdkDir, "README.md"), "utf-8");

/** Namespace names the README lists in its pod-scoped-namespaces sentence. */
function documentedNamespaces(): string[] {
  const sentence = readme
    .split("\n")
    .find((line) => line.startsWith("Pod-scoped namespaces include"));
  if (!sentence) {
    throw new Error("README.md no longer lists the pod-scoped namespaces");
  }
  return [...new Set([...sentence.matchAll(/`([a-zA-Z][a-zA-Z0-9]*)`/g)].map((m) => m[1]))];
}

describe("README pod-scoped namespaces", () => {
  it("names only namespaces LemmaClient actually has", () => {
    const client = new LemmaClient({
      apiUrl: "https://api.example.test",
      authUrl: "https://auth.example.test",
      podId: "pod-1",
    });

    const missing = documentedNamespaces().filter(
      (name) => (client as unknown as Record<string, unknown>)[name] === undefined,
    );

    expect(missing).toEqual([]);
  });

  it("covers enough of the client to be worth reading", () => {
    expect(documentedNamespaces().length).toBeGreaterThan(8);
  });
});

describe("README registry count", () => {
  it("agrees with registry.json on how many blocks ship", () => {
    // AGENTS.md carries the same claim and the same guard; the two disagreed
    // once, which is how a reader learns to trust neither.
    const registry = JSON.parse(
      readFileSync(path.join(sdkDir, "registry.json"), "utf-8"),
    ) as { items: { name: string }[] };
    // `lemma-ui` is the shared primitive layer, not a block.
    const blocks = registry.items.filter((item) => item.name !== "lemma-ui");

    expect(readme).toContain(`ships ${blocks.length} canonical blocks`);
  });
});

describe("README local paths", () => {
  it("only tells the reader to cd into directories that exist", () => {
    // A `cd` into a directory that was deleted is where a reader stops
    // trusting the rest of the page.
    const targets = [...readme.matchAll(/^cd\s+([^\s`"']+)\s*$/gm)]
      .map((match) => match[1])
      .filter((target) => !target.startsWith("/") && !target.startsWith("<"));

    const missing = targets.filter((target) => !existsSync(path.join(sdkDir, target)));

    expect(missing).toEqual([]);
  });
});

describe("config comments that name a CI gate", () => {
  it("point at a file that exists", () => {
    // tsconfig.test.json claimed a `sdk-checks.yml` that had never existed, so
    // the one thing that makes a type-level assertion in a test file worth
    // writing -- that something checks it -- was unverifiable from here.
    const repoRoot = path.resolve(sdkDir, "..");
    const configs = ["tsconfig.json", "tsconfig.test.json", "tsconfig.bundle.json"];

    const dangling = configs.flatMap((config) => {
      const text = readFileSync(path.join(sdkDir, config), "utf-8");
      return [...text.matchAll(/\.github\/[A-Za-z0-9_./-]+/g)]
        .map((match) => match[0].replace(/\.$/, "")) // sentence-final period
        .filter((reference) => !existsSync(path.join(repoRoot, reference)))
        .map((reference) => `${config} -> ${reference}`);
    });

    expect(dangling).toEqual([]);
  });
});
