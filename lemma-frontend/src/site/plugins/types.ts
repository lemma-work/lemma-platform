/** The shapes behind the plugin catalog: `tables.ts` names the shared tables,
 *  `parts.ts` says what each plugin's bundle holds, `catalog.ts` says what it
 *  is for and what it still lacks. */

/** A plugin's address under `/templates/`. Every slug a plugin names (in
 *  `links`, `needs` or `basis`) is checked against the catalog by
 *  `tests/plugin-catalog.test.ts`. */
export type PluginSlug = string;

/** A base makes shared tables other plugins stand on; a tool works for any
 *  job; a job plugin is for one kind of work. */
export type PluginKind = "base" | "tool" | "job";

/** How far a plugin is from installing: `ready` installs today; `runs` works
 *  as a Lemma but isn't published; `partly` has a piece built somewhere;
 *  `planned` is drawn and not built. */
export type Stage = "ready" | "runs" | "partly" | "planned";

/** A table more than one plugin reads or writes, matched by what it holds
 *  rather than by its name. */
export type TableKind =
    | "person"
    | "company"
    | "touch"
    | "commitment"
    | "task"
    | "project"
    | "meeting"
    | "brand"
    | "metric"
    | "deal"
    | "campaign"
    | "content"
    | "mention"
    | "feedback"
    | "feature"
    | "ticket"
    | "repo"
    | "pr"
    | "issue"
    | "signal"
    | "employee"
    | "candidate"
    | "vendor"
    | "request";

export type Job =
    | "everyone"
    | "sales"
    | "marketing"
    | "product"
    | "founder"
    | "ops"
    | "hr"
    | "pm"
    | "dev"
    | "support";

/** What a cover draws for an app until the app ships its own picture. */
export type AppShape =
    | "queue"
    | "list"
    | "board"
    | "cards"
    | "canvas"
    | "feed"
    | "mail"
    | "calendar"
    | "deck"
    | "page";

export interface TablePart {
    name: string;
    kind: TableKind | null;
    /** Row-level security: each person sees only their own rows. */
    eachPersonsOwn: boolean;
    columns: number;
    key: string[];
    seed?: { rows: number; ids: boolean };
}

export interface AppPart {
    name: string;
    title: string;
    shape: AppShape;
    line: string;
    screens: string[];
    /** False when the app lives beside the bundle rather than in it. */
    inBundle: boolean;
    /** Whether the app ships its own `/.lemma/cover.png`. */
    cover: boolean;
}

export interface NamedPart {
    name: string;
    line: string;
}

export interface WorkflowPart extends NamedPart {
    starts: string;
}

export interface SchedulePart {
    name: string;
    when: string;
    runs: string;
}

export interface ConnectorPart {
    service: string;
    /** PER_MEMBER: each person connects their own. SHARED: one for everyone. */
    mode: "PER_MEMBER" | "SHARED";
    required: boolean;
    why: string;
    /** False when the code uses it but the bundle never asks for it. */
    declared: boolean;
}

export interface BundleParts {
    tables: TablePart[];
    apps: AppPart[];
    agents: NamedPart[];
    functions: NamedPart[];
    workflows: WorkflowPart[];
    schedules: SchedulePart[];
    connectors: ConnectorPart[];
    skills: NamedPart[];
}

/** What a plugin still lacks before it installs cleanly. The first seven are
 *  read off the data (`gapsOf` in `catalog.ts`); the rest are written down. */
export type GapKind =
    | "not-built"
    | "no-connector"
    | "needs-unready"
    | "no-install-source"
    | "app-outside-bundle"
    | "undeclared-account"
    | "no-cover"
    | "split-from-bundle"
    | "table-mismatch"
    | "name-collision"
    | "set-up-outside-import"
    | "made-for-one-person"
    | "not-yet-shared"
    | "missing-piece"
    | "skill-collision"
    | "seed-without-ids";

export interface Gap {
    kind: GapKind;
    text: string;
}

/** How a plugin stands with another: `works` today, `planned` is drawn but
 *  not built, `clashes` breaks when both are installed. */
export interface Link {
    slug: PluginSlug;
    state: "works" | "planned" | "clashes";
    why: string;
}

/** An account a plugin asks for: any one of `any` will do. */
export interface AccountNeed {
    any: string[];
    required: boolean;
}

/** Something that already exists that a plugin starts from: a piece of
 *  another plugin, a GitHub template, or a plain note. */
export interface Basis {
    text: string;
    plugin?: PluginSlug;
    /** A slug in `PUBLIC_TEMPLATES`, which installs today. */
    template?: string;
}

export interface Plugin {
    slug: PluginSlug;
    name: string;
    kind: PluginKind;
    /** The jobs whose shelf shows it, the first being where it belongs. */
    jobs: Job[];
    headline: string;
    lede: string;
    /** What it does, line by line; only plugins read from a bundle have it. */
    does?: string[];
    makes: TableKind[];
    uses: TableKind[];
    /** Tables it reads when another plugin has made them, and works without. */
    reads?: TableKind[];
    /** Plugins it cannot run without, beyond the ones its tables pull in. */
    needs?: PluginSlug[];
    /** Suggests a Lemma only its maker can see. */
    private?: boolean;
    /** Where the install flow imports it from; null until it is published. */
    install: { owner: string; repo: string } | null;
    /** Works as a Lemma today, though its parts aren't read into `parts.ts`. */
    runs?: boolean;
    basis?: Basis[];
    /** The accounts it would ask for. Plugins read from a bundle show the
     *  bundle's own connectors instead. */
    accounts?: AccountNeed[];
    /** Services it wants that Lemma can't connect to yet. */
    missing?: string[];
    links: Link[];
    gaps: Gap[];
}
