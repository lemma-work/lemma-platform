/** Reading a server-described form, and writing back only what changed.
 *
 *  Every connector says what it needs in JSON Schema — an install's config, an
 *  account's credentials, an organization's own OAuth app — and they are not
 *  the same fields from one connector to the next. So this renders what the
 *  server describes rather than a form per connector, the way the notification
 *  form and the record editor already do for their own server-owned shapes.
 *
 *  The subset is deliberate. These schemas are hand-written catalogue entries,
 *  not arbitrary JSON Schema: a flat object of strings, secrets, numbers,
 *  booleans, enums, and the one header map. Anything outside that is shown as
 *  a plain text field rather than silently dropped, because a field nobody can
 *  fill is worse than an ugly one.
 */

/** What a secret reads back as. The API masks on read — every connector's
 *  config and credentials — so this string arrives in place of real values
 *  more often than not. See `unchangedSecret`. */
export const REDACTED = "********";

export type FieldKind = "text" | "secret" | "number" | "boolean" | "choice" | "headers";

export interface Field {
    name: string;
    label: string;
    kind: FieldKind;
    required: boolean;
    description?: string;
    placeholder?: string;
    /** For `choice`. */
    options?: string[];
    /** What the schema says to start with. */
    initial?: unknown;
}

interface RawSchema {
    type?: string;
    properties?: Record<string, RawProperty>;
    required?: string[];
}

interface RawProperty {
    type?: string | string[];
    title?: string;
    description?: string;
    format?: string;
    writeOnly?: boolean;
    enum?: unknown[];
    default?: unknown;
    examples?: unknown[];
    additionalProperties?: unknown;
}

/** A location is not a credential.
 *
 *  Mirrors the API's own rule. Without it every `authorization_endpoint` and
 *  `token_endpoint` — which an operator needs to read, and which appear in the
 *  authorize URL anyway — would be drawn as a masked password box.
 */
const PUBLIC_SUFFIXES = ["_endpoint", "_url", "_uri"];
const SECRET_WORDS = /secret|token|password|api_?key|credential|client_secret|authorization/i;

export function isSecretName(name: string): boolean {
    const lower = name.trim().toLowerCase();
    if (PUBLIC_SUFFIXES.some((suffix) => lower.endsWith(suffix))) return false;
    return SECRET_WORDS.test(lower);
}

/** Words to a label, when the schema gave no title. */
function labelFor(name: string, property: RawProperty): string {
    if (property.title) return property.title;
    const words = name.replace(/[_-]+/g, " ").trim();
    return words.charAt(0).toUpperCase() + words.slice(1);
}

function kindFor(name: string, property: RawProperty): FieldKind {
    if (Array.isArray(property.enum) && property.enum.length > 0) return "choice";
    const type = Array.isArray(property.type) ? property.type[0] : property.type;
    if (type === "boolean") return "boolean";
    if (type === "integer" || type === "number") return "number";
    /* The tenant-keyed maps: `extra_headers` on an MCP install,
       `default_headers` on an OpenAPI one, both described as "anything the
       server needs beyond the token". Their keys are the tenant's to choose,
       so they are pairs rather than a fixed set of inputs. */
    if (type === "object") return "headers";
    if (property.format === "password" || property.writeOnly || isSecretName(name)) return "secret";
    return "text";
}

/** The fields a schema asks for, in the order it lists them. */
export function fields(schema: unknown): Field[] {
    const raw = (schema ?? {}) as RawSchema;
    const properties = raw.properties;
    if (!properties || typeof properties !== "object") return [];
    const required = new Set(Array.isArray(raw.required) ? raw.required.map(String) : []);
    return Object.entries(properties).map(([name, property]) => {
        const one = (property ?? {}) as RawProperty;
        const example = Array.isArray(one.examples) ? one.examples[0] : undefined;
        return {
            name,
            label: labelFor(name, one),
            kind: kindFor(name, one),
            required: required.has(name),
            description: one.description,
            placeholder: example === undefined || example === null ? undefined : String(example),
            options: Array.isArray(one.enum) ? one.enum.map(String) : undefined,
            initial: one.default,
        };
    });
}

export type Values = Record<string, unknown>;

/** What to start a form with: the schema's defaults, and nothing invented. */
export function blank(list: Field[], existing?: Values | null): Values {
    const values: Values = {};
    for (const field of list) {
        const held = existing?.[field.name];
        if (held !== undefined) values[field.name] = held;
        else if (field.initial !== undefined) values[field.name] = field.initial;
        else values[field.name] = field.kind === "boolean" ? false : field.kind === "headers" ? {} : "";
    }
    return values;
}

/** A secret the reader never saw, and therefore cannot have edited.
 *
 *  This is the trap the whole file exists for. Reads come back masked, so a
 *  form that renders what it fetched and submits what it rendered writes
 *  `********` into the credential and breaks the connector — and the only sign
 *  is that it stops working later, against a value nobody can read back to
 *  check. A field still holding the mask is a field left alone.
 */
export function unchangedSecret(value: unknown): boolean {
    return typeof value === "string" && value.trim() === REDACTED;
}

/** What is missing, by field name, before anything is sent.
 *
 *  Required is the only rule enforced here on purpose. The server validates
 *  each config against the connector's own schema and guards the URLs besides,
 *  and a second opinion in the client is a second thing to drift.
 */
export function problems(list: Field[], values: Values): Record<string, string> {
    const found: Record<string, string> = {};
    for (const field of list) {
        if (!field.required) continue;
        const value = values[field.name];
        const empty = value === undefined || value === null
            || (typeof value === "string" && !value.trim())
            || (field.kind === "headers" && Object.keys((value ?? {}) as object).length === 0);
        if (empty) found[field.name] = field.label + " is needed.";
    }
    return found;
}

/** The payload to send: trimmed, typed, and with untouched secrets left out.
 *
 *  Leaving a masked secret out rather than sending it is what makes an edit
 *  form safe against a redacted read — the server keeps what it already holds
 *  for a key it is not given.
 */
export function payload(list: Field[], values: Values): Values {
    const out: Values = {};
    for (const field of list) {
        const value = values[field.name];
        if (field.kind === "secret" && unchangedSecret(value)) continue;
        if (field.kind === "boolean") { out[field.name] = Boolean(value); continue; }
        if (field.kind === "headers") {
            const pairs = Object.entries((value ?? {}) as Record<string, unknown>)
                .filter(([key, held]) => key.trim() && !unchangedSecret(held));
            if (pairs.length > 0) out[field.name] = Object.fromEntries(pairs.map(([k, v]) => [k.trim(), String(v)]));
            continue;
        }
        if (typeof value === "string") {
            const trimmed = value.trim();
            /* An optional field left blank is not a field set to "". Sending
               the empty string writes it, which for a header or a spec URL is
               a different configuration from not having one. */
            if (!trimmed) { if (field.required) out[field.name] = ""; continue; }
            out[field.name] = field.kind === "number" ? Number(trimmed) : trimmed;
            continue;
        }
        if (value !== undefined && value !== null) out[field.name] = value;
    }
    return out;
}
