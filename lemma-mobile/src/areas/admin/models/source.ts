import type { ProviderProtocol } from "@web/org/provider-draft";
import type { Choice, Computer, Runtime, RuntimeTest } from "@web/data/runtimes";

/** A provider's key, as the "Connect a key" screen sends it. The key itself
 *  goes to the server once and is never read back, kept or logged here. */
export interface NewKey {
    protocol: ProviderProtocol;
    name: string;
    baseUrl: string;
    apiKey: string;
    /** Empty: the provider's own list is used. */
    models: string[];
    /** Of those, the ones that read pictures (OpenAI-style routes only). */
    visionModels: string[];
}

/** Models: the organization's, its provider keys and coding agents, and the
 *  one a Lemma runs on (PARITY.md, F; the web's `org/models.tsx`,
 *  `shell/runs-on.tsx`).
 *
 *  Everything this part reads or changes, in the web's own shapes
 *  (`web/runtimes.ts`, which reads the wire). The live side is `live.ts`, the
 *  sample side `sample.ts`; screens reach it through `hooks.ts`, never the
 *  SDK directly, so sample data and a live account are the same screens.
 *  Coding agents are read here, never changed: they are set up on their own
 *  computer. */
export interface ModelsSource {
    /** Everything the organization can run on, retired ones included. */
    runtimes(orgId: string): Promise<Runtime[]>;
    /** One runtime read on its own, which alone carries a coding agent's
     *  options (effort, mode) as its computer publishes them now. */
    runtime(orgId: string, runtimeId: string): Promise<Runtime | null>;
    /** What a Lemma that names nothing runs on: the organization's default,
     *  else whatever the server falls back to. */
    inherited(orgId: string): Promise<Choice | null>;
    /** The default the organization chose, or null when nobody has. */
    organizationDefault(orgId: string): Promise<Choice | null>;
    /** Choose the organization's default; an empty model follows the key's
     *  own default as it changes. */
    setOrganizationDefault(orgId: string, choice: Choice | null): Promise<void>;
    /** Ask a saved key's provider to answer once. Never the provider's own
     *  words: the server's fixed sentence. */
    test(orgId: string, runtimeId: string): Promise<RuntimeTest>;
    /** Connect a provider's key, for the whole organization. */
    addKey(orgId: string, key: NewKey): Promise<void>;
    /** Retire a runtime: out of every picker, readable, restorable. */
    archive(orgId: string, runtimeId: string): Promise<void>;
    restore(orgId: string, runtimeId: string): Promise<void>;
    /** The computers paired to this account, each with the coding agents it
     *  found on itself. A removed computer is not listed. */
    computers(): Promise<Computer[]>;
    /** What a Lemma runs on when an agent names nothing: its own pick, or
     *  null to follow the organization's default. */
    lemmaChoice(podId: string): Promise<Choice | null>;
    setLemmaChoice(podId: string, choice: Choice | null): Promise<void>;
}
