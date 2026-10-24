import type { Lemma, Org } from "../../../data/types";
import type { ConnectorChoice, ImportStatusResponse } from "@web/site/import-types";
import type { PendingInstall } from "@web/site/import/pending-install";

export type { ConnectorPlan, ImportStatusResponse, PlanStep, VariableSpec } from "@web/site/import-types";
export type { PendingInstall } from "@web/site/import/pending-install";

/** A public repository's README as it was read: its words, and the branch
 *  its relative links and pictures are resolved against. */
export interface Readme {
    markdown: string;
    branch: string;
}

/** Templates, installing from GitHub, and remixing an app (PARITY.md, G; the
 *  web's `site/import/install-panel.tsx` and `site/actions.tsx`).
 *
 *  Everything this part reads or changes. The live side is `live.ts`, the
 *  sample side `sample.ts` (a whole install in memory); screens reach it
 *  through `hooks.ts`, never the SDK directly. The organization's accounts
 *  are read through Settings' own connectors part, not here. */
export interface InstallSource {
    /** A public repository's README, read without credentials: null when it
     *  cannot be read (no README, a private repository, no network). */
    readme(owner: string, repo: string): Promise<Readme | null>;

    /** An organization for somebody who has none. `derived` when the name is
     *  the one the app made up, so the server may make it unique rather than
     *  refuse a name the person never chose. */
    createOrg(name: string, derived: boolean): Promise<Org>;
    /** A new Lemma in an organization, by name. */
    createLemma(orgId: string, name: string): Promise<Lemma>;

    /** Start reading what a repository brings: the job that fetches and plans
     *  it, through the Lemma's `github` connector as the person installing. */
    startImport(podId: string, input: { owner: string; repo: string }): Promise<ImportStatusResponse>;
    readImport(podId: string, importId: string): Promise<ImportStatusResponse>;
    /** Install everything in the plan, with the answers given and what was
     *  picked for each connector it brings. */
    applyImport(podId: string, importId: string, input: { variables: Record<string, string>; connectors: Record<string, ConnectorChoice>; confirmDestructive: boolean }): Promise<ImportStatusResponse>;
    /** Withdraw a job waiting on review, or stop one installing. */
    cancelImport(podId: string, importId: string): Promise<void>;

    /** The install somebody started and has not finished, kept on the phone
     *  (the web's rules, `web/pending-install.ts`: 24 hours, an install's own
     *  path only). */
    pending(): Promise<PendingInstall | null>;
    rememberPending(install: PendingInstall): Promise<void>;
    forgetPending(): Promise<void>;
}
