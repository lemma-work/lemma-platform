/** Which form to show, and which flow a connector is actually on.
 *
 *  Connecting is not one thing. The catalogue holds four kinds and two auth
 *  schemes, and assuming the single combination in front of you — ask for an
 *  authorize URL, open it, done — leaves every other combination at a button
 *  that starts a flow with no second half.
 *
 *  Nothing here is guesswork about a connector. Every answer is read off what
 *  the catalogue says about its own kinds.
 */

/** How an install authenticates, discovers and executes.
 *
 *  `composio` is the brokered catalogue — hundreds of toolkits reached through
 *  Lemma's own Composio account. The other three are things an organization
 *  points at itself, which is why they share one catalogue entry each: every
 *  MCP server is the `mcp` entry, every database is `sql`, every REST API is
 *  `openapi`.
 */
export type Kind = string;

/** The kinds this build knows the shape of. A deployment is free to ship
 *  another, and a union would make that build's catalogue unassignable rather
 *  than merely unfamiliar — so these are values to compare against, not a type
 *  that forbids the rest. */
export const COMPOSIO = "composio";
export type AuthScheme = "OAUTH2" | "API_KEY" | "NOAUTH";
export type Discovery = "none" | "mcp" | "openapi";

/** The kinds where the organization supplies the address. Necessary for a
 *  connector to be one somebody points somewhere, and not sufficient: `http`
 *  also carries GitHub, Slack and Gmail (signed into, over Lemma's own OAuth
 *  client) and the WhatsApp and Telegram bots (a token, and no address at
 *  all). See `isTenantConfigured`. */
const ADDRESSED_KINDS: ReadonlySet<string> = new Set(["http", "sql", "mcp"]);

export interface ConnectorKind {
    kind: Kind;
    auth_scheme?: AuthScheme;
    /** JSON Schema for an install's own `config`. On a Composio toolkit that
     *  Composio holds no credentials for, this is instead the *end user's*
     *  credential form — see `connectSchema`. */
    config_schema?: unknown;
    /** JSON Schema for a connected account's credentials. */
    credential_schema?: unknown;
    /** Composio only: what the *organization* supplies when Composio manages
     *  no credentials for this toolkit — a client id and secret, and whatever
     *  else the toolkit wants. */
    install_config_schema?: unknown;
    supports_org_custom_oauth?: boolean;
    /** The endpoints an organization's own OAuth app would be sent through.
     *  Without them "bring your own app" can only strand an install. */
    oauth2_defaults?: unknown;
    /** Whether Lemma's own credentials can install this at all. */
    system_default_available?: boolean;
    discovery?: Discovery;
    toolkit_slug?: string;
}

export interface CatalogEntry {
    id: string;
    title: string;
    description?: string | null;
    icon?: string | null;
    kinds?: ConnectorKind[];
}

/** An install, as `GET .../auth-configs` returns it. */
export interface Install {
    id: string;
    connector_id: string;
    kind: string;
    name: string;
    status?: string;
    is_default?: boolean;
    config_source?: string;
    /** How *this install* signs in, which is not always what its connector's
     *  catalogue entry says. `mcp` is one entry standing for every server a
     *  tenant may point at: the entry says API_KEY, and an install whose
     *  server described its own authorization when it was created answers
     *  OAUTH2 here. The API's own description says to branch on this. */
    auth_scheme?: AuthScheme | null;
    config?: Record<string, unknown> | null;
}

function hasProperties(schema: unknown): boolean {
    const properties = (schema as { properties?: unknown } | null)?.properties;
    return Boolean(properties && typeof properties === "object" && Object.keys(properties).length > 0);
}

/** Whether this kind is something the organization points somewhere itself —
 *  a database, an OpenAPI spec, an MCP server — rather than an app it signs
 *  into.
 *
 *  "Not Composio" was the old test, and it is wrong for most of what is not
 *  Composio. GitHub, Slack and Gmail are `http` with Lemma's own OAuth client;
 *  WhatsApp and Telegram are `http` with a bot token. Treated as bring-your-own,
 *  each of them offered "Add one" instead of "Connect", and the add form asked
 *  for an address none of them has. The install schema settles it: the backend
 *  hands every non-OAuth connector without an address an empty one.
 */
export function isTenantConfigured(kind: ConnectorKind | null | undefined): boolean {
    if (!kind || !ADDRESSED_KINDS.has(kind.kind)) return false;
    if (kind.kind === "http" && kind.auth_scheme === "OAUTH2") return false;
    return hasProperties(installSchema(kind));
}

/** The kind of this entry that is pointed somewhere, if it has one. */
export function tenantKind(entry: CatalogEntry): ConnectorKind | null {
    return (entry.kinds ?? []).find((one) => isTenantConfigured(one)) ?? null;
}

/** The entries an organization points somewhere itself, rather than picks.
 *
 *  Tested on the kind rather than on the id, because the ids are catalogue
 *  data and this is a rule about what a connector *is*: one entry standing for
 *  every server, database or API of that sort.
 */
export function isBringYourOwn(entry: CatalogEntry): boolean {
    return tenantKind(entry) !== null;
}

/** The kind a fresh install should take when nobody picked one.
 *
 *  Composio first where it is on offer, the same default the backend and the
 *  harness use. Returning nothing for a connector with two kinds — the old
 *  rule — left every such connector at "has not described what it needs". */
export function primaryKind(entry: CatalogEntry): ConnectorKind | null {
    const kinds = entry.kinds ?? [];
    return kinds.find((one) => one.kind === COMPOSIO) ?? kinds[0] ?? null;
}

export function kindNamed(entry: CatalogEntry, kind: string | null | undefined): ConnectorKind | null {
    const kinds = entry.kinds ?? [];
    if (kind) return kinds.find((one) => one.kind === kind) ?? null;
    /* No kind named: only an unambiguous answer counts. The API takes `kind`
       as optional "when the connector offers only one", and a client picking
       the first of several would install something nobody asked for. */
    return kinds.length === 1 ? kinds[0] : null;
}

/** The kind an account on this install — or on the install about to be made —
 *  runs under. */
export function kindFor(entry: CatalogEntry, install: Install | null): ConnectorKind | null {
    return install ? kindNamed(entry, install.kind) : primaryKind(entry);
}

/** Whether Lemma can create this install with nothing from the organization.
 *
 *  `system_default_available` alone does not answer it: the importer sets it
 *  from "not OAuth", which says nothing about a database that still needs a
 *  host. A managed Composio toolkit and an OAuth app Lemma holds a client for
 *  need nothing; anything else needs whatever its install schema requires. */
export function canInstallWithDefaults(kind: ConnectorKind | null): boolean {
    if (!kind || !kind.system_default_available) return false;
    if (kind.kind === COMPOSIO || kind.auth_scheme === "OAUTH2") return true;
    if (isTenantConfigured(kind)) return false;
    const schema = installSchema(kind) as { required?: unknown } | null;
    return !(Array.isArray(schema?.required) && schema.required.length > 0);
}

/** A name for an install that does not collide with the ones already there.
 *
 *  Install names are unique per organization and an unnamed one is named after
 *  its connector — so a second unnamed install of anything the organization
 *  already has is refused. */
export function freshInstallName(base: string, taken: Iterable<string>): string {
    const used = new Set(taken);
    if (!used.has(base)) return base;
    for (let n = 2; ; n += 1) if (!used.has(base + "-" + n)) return base + "-" + n;
}

/** What the *organization* fills in to create an install.
 *
 *  Two different schemas wear the name `config_schema` depending on the kind,
 *  and handing back the wrong one empties the connect dialog for every
 *  API-key toolkit — so this is the one place that chooses.
 *
 *  - A Composio toolkit: `install_config_schema`, the org's own OAuth app.
 *    Its `config_schema` belongs to the end user and is not this form.
 *  - Anything else: `config_schema` is the install — a server URL, a spec, a
 *    connection string.
 */
export function installSchema(kind: ConnectorKind | null): unknown {
    if (!kind) return null;
    return kind.kind === COMPOSIO ? kind.install_config_schema ?? null : kind.config_schema ?? null;
}

/** What the *person connecting an account* fills in.
 *
 *  `credential_schema` when the kind declares one. Otherwise a Composio
 *  toolkit's `config_schema`, which for a non-OAuth toolkit is exactly this
 *  form — Composio's own `connected_account_initiation` fields.
 */
export function connectSchema(kind: ConnectorKind | null): unknown {
    if (!kind) return null;
    if (kind.credential_schema) return kind.credential_schema;
    return kind.kind === COMPOSIO ? kind.config_schema ?? null : null;
}

/** How to connect an account against an install.
 *
 *  `redirect` is the browser round trip; `credentials` is a form submitted
 *  straight to the API. The install's own scheme decides, and falls back to
 *  the catalogue only when an install has not said.
 *
 *  Only OAuth2 redirects. `NOAUTH` is a credential post with nothing in it —
 *  the backend refuses a connect request for anything but OAuth, so sending
 *  one down the redirect made those connectors impossible to connect.
 */
export type ConnectRoute = "redirect" | "credentials";

export function connectRoute(install: Install | null, kind: ConnectorKind | null): ConnectRoute {
    const scheme = install?.auth_scheme ?? kind?.auth_scheme ?? "OAUTH2";
    return scheme === "OAUTH2" ? "redirect" : "credentials";
}

/** Whether an organization has to bring its own OAuth app before anyone can
 *  connect. `system_default_available` is a per-toolkit answer and is read
 *  rather than assumed: pinned true, an unconnectable toolkit advertises a
 *  Connect button that cannot finish. */
export function needsOwnApp(kind: ConnectorKind | null): boolean {
    if (!kind || kind.kind !== COMPOSIO) return false;
    return kind.system_default_available === false;
}

/** Whether "use your own app" is on offer beside Lemma's.
 *
 *  Never for a managed Composio toolkit: it runs on Lemma's Composio account
 *  and the backend refuses an org-supplied install of it. Otherwise both
 *  halves, as in the harness — an organization's client id and secret are
 *  useless without endpoints to send people through, and those come from the
 *  catalogue. */
export function canBringOwnApp(kind: ConnectorKind | null): boolean {
    if (!kind) return false;
    if (kind.kind === COMPOSIO) return needsOwnApp(kind) && hasProperties(installSchema(kind));
    return Boolean(kind.supports_org_custom_oauth && kind.oauth2_defaults);
}

/** What re-reading an install's operations actually did.
 *
 *  A count alone cannot say. A connector with nothing to advertise, a kind
 *  whose operations are fixed, and a server that refused the listing all
 *  report zero, and they need different things from the reader — so the API
 *  reports a status beside the number and this says each one out loud.
 */
export function discoveryNote(
    status: string | null | undefined,
    count: number | null | undefined,
    error?: string | null,
): string {
    const found = typeof count === "number" ? count : 0;
    if (status === "failed") {
        return error ? "The server refused the listing: " + error : "The server refused to list its operations.";
    }
    if (status === "not_applicable") return "This connector's operations are fixed, so there was nothing to re-read.";
    if (found === 0) return "Connected, but the server advertised no operations.";
    return "Found " + found + (found === 1 ? " operation." : " operations.");
}

/** What went wrong with a connector call, in words somebody can act on.
 *
 *  The one worth translating is the role refusal. Making an install needs an
 *  owner or an editor, and the backend answers anyone else with a 404 naming
 *  the organization's uuid — deliberately vague about membership, and
 *  unreadable on a connector card. Everything else is passed through: the API
 *  writes its messages for people. */
export const NEEDS_EDITOR = "Only an organization owner or editor can set up a new connector. Ask one of them to enable it — then anybody can connect an account.";

export function connectorProblem(problem: unknown, fallback: string): string {
    const code = (problem as { code?: unknown } | null)?.code;
    const message = problem instanceof Error ? problem.message : typeof problem === "string" ? problem : "";
    if (code === "ORGANIZATION_CONNECTORS_NOT_FOUND" || /No connectors are available in organization/i.test(message)) {
        return NEEDS_EDITOR;
    }
    return urlRefusal(message) ?? (message || fallback);
}

/** Why a URL was refused, said usefully.
 *
 *  Every URL an organization supplies is checked against the API's own guard
 *  before anything is stored, so a server on a private network is refused —
 *  which is correct, and reads as a generic failure unless somebody says what
 *  it was.
 */
export function urlRefusal(message: string): string | null {
    if (!/unsafe|private|loopback|not allowed|blocked|resolve/i.test(message)) return null;
    return "That address was refused: it has to be reachable from the internet, "
        + "not a private or loopback address.";
}

/** Whether a failure is the backend saying this connector has no OAuth app to
 *  sign in through — the case with a fix on a local install, where the app is
 *  the machine's to add. Matched on the backend's wording in
 *  `auth_install_resolver.py`. */
export function oauthAppMissing(message: string | null | undefined): boolean {
    return /needs an OAuth app/i.test(message ?? "");
}
