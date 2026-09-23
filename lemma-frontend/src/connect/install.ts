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
export type AuthScheme = "OAUTH2" | "API_KEY";
export type Discovery = "none" | "mcp" | "openapi";

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

/** The entries an organization points somewhere itself, rather than picks.
 *
 *  Tested on the kind rather than on the id, because the ids are catalogue
 *  data and this is a rule about what a connector *is*: one entry standing for
 *  every server, database or API of that sort.
 */
export function isBringYourOwn(entry: { kinds?: { kind: string }[] }): boolean {
    return (entry.kinds ?? []).some((one) => one.kind !== COMPOSIO);
}

export function kindNamed(entry: CatalogEntry, kind: string | null | undefined): ConnectorKind | null {
    const kinds = entry.kinds ?? [];
    if (kind) return kinds.find((one) => one.kind === kind) ?? null;
    /* No kind named: only an unambiguous answer counts. The API takes `kind`
       as optional "when the connector offers only one", and a client picking
       the first of several would install something nobody asked for. */
    return kinds.length === 1 ? kinds[0] : null;
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
 */
export type ConnectRoute = "redirect" | "credentials";

export function connectRoute(install: Install | null, kind: ConnectorKind | null): ConnectRoute {
    const scheme = install?.auth_scheme ?? kind?.auth_scheme ?? "OAUTH2";
    return scheme === "API_KEY" ? "credentials" : "redirect";
}

/** Whether an organization has to bring its own OAuth app before anyone can
 *  connect. `system_default_available` is a per-toolkit answer and is read
 *  rather than assumed: pinned true, an unconnectable toolkit advertises a
 *  Connect button that cannot finish. */
export function needsOwnApp(kind: ConnectorKind | null): boolean {
    if (!kind || kind.kind !== COMPOSIO) return false;
    return kind.system_default_available === false;
}

export function canBringOwnApp(kind: ConnectorKind | null): boolean {
    return Boolean(kind?.supports_org_custom_oauth);
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
