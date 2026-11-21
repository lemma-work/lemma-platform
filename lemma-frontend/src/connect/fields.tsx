import { useState } from "react";
import { Problem } from "@/outcomes/calm";
import { MoreSettings, NOT_A_LOGIN, SecretInput } from "@/ui/form";
import { CloseIcon, PlusIcon } from "@/ui/icons";
import { REDACTED, unchangedSecret, type Field, type Values } from "./schema";

/** A server-described form, drawn the one way every form here is drawn.
 *
 *  Every credential and setting a connector or a place asks for comes from a
 *  schema the server owns — a connection's credentials, an install's
 *  address, an organization's own app — so this renders what it describes
 *  rather than a form per connector. The rules live here once (kit.css,
 *  "forms"): a label above each field in plain words, the schema's own
 *  description under it where it has one, "(optional)" on the fields that
 *  are rather than a star on the ones that are not, secrets hidden with Show
 *  beside them, and the plumbing — an API address, a webhook's check token,
 *  a port — folded under More settings.
 *
 *  What it draws changes nothing about what is sent: values go back through
 *  `onChange` keyed by the schema's own names, and `payload` builds the body.
 */
export function SchemaFields({ list, values, problems, onChange, disabled }: {
    list: Field[];
    values: Values;
    problems: Record<string, string>;
    onChange: (name: string, value: unknown) => void;
    disabled?: boolean;
}) {
    const main = list.filter((field) => !field.more);
    const more = list.filter((field) => field.more);
    /* Closed, unless one of them already holds something of its own — an
       install being edited that was pointed somewhere on purpose. */
    const [opened] = useState(() => more.some((field) => setOnPurpose(field, values[field.name])));
    const one = (field: Field) => (
        <OneField key={field.name} field={field} value={values[field.name]} problem={problems[field.name]}
            disabled={disabled} onChange={onChange} />
    );
    return (
        <div className="kform">
            {main.map(one)}
            {more.length > 0 && <MoreSettings open={opened}>{more.map(one)}</MoreSettings>}
        </div>
    );
}

/** A value somebody chose, rather than the schema's default or nothing. */
function setOnPurpose(field: Field, value: unknown): boolean {
    if (value === undefined || value === null || value === "" || value === false) return false;
    if (typeof value === "object" && Object.keys(value as object).length === 0) return false;
    return JSON.stringify(value) !== JSON.stringify(field.initial);
}

/** The schema's description, as the line under a field. "Optional." is
 *  already said beside the label, so it is not said twice. */
function helpFor(field: Field): string | null {
    const said = (field.description ?? "").replace(/^\s*optional\.\s*/i, "").trim();
    return said || null;
}

function OneField({ field, value, problem, disabled, onChange }: {
    field: Field;
    value: unknown;
    problem?: string;
    disabled?: boolean;
    onChange: (name: string, value: unknown) => void;
}) {
    const id = "f-" + field.name;
    const help = helpFor(field);
    const helpId = help ? id + "-help" : undefined;
    if (field.kind === "boolean") {
        /* A tick box carries its own label, so a second one above it would
           only print the name twice. */
        return (
            <div className="kfield">
                <label className="kset-check">
                    <input
                        id={id}
                        type="checkbox"
                        checked={Boolean(value)}
                        disabled={disabled}
                        aria-describedby={helpId}
                        onChange={(event) => onChange(field.name, event.target.checked)}
                    />
                    <span>
                        {field.label}
                        {help && <em id={helpId}>{help}</em>}
                    </span>
                </label>
                {problem && <Problem>{problem}</Problem>}
            </div>
        );
    }
    return (
        <div className="kfield">
            <label htmlFor={field.kind === "headers" ? undefined : id}>
                {field.label}
                {!field.required && <span className="kfield__opt"> (optional)</span>}
            </label>
            <Control field={field} id={id} helpId={helpId} value={value} invalid={Boolean(problem)} disabled={disabled} onChange={onChange} />
            {/* Under the box rather than between it and its label: some help
                runs to three lines, and above the input it pushed the label a
                paragraph away from the thing it names. */}
            {problem && <Problem>{problem}</Problem>}
            {help && <small id={helpId}>{help}</small>}
        </div>
    );
}

function Control({ field, id, helpId, value, invalid, disabled, onChange }: {
    field: Field;
    id: string;
    helpId?: string;
    value: unknown;
    invalid: boolean;
    disabled?: boolean;
    onChange: (name: string, value: unknown) => void;
}) {
    /* Whether a stored secret stood here when the form opened. Only then does
       leaving the box empty mean "keep it": on a new connection there is
       nothing stored, and putting the mask back wrote "********" into an empty
       required field — which passed the required check, was dropped from the
       payload as untouched, and reached the server as no token at all. */
    const [stored] = useState(() => unchangedSecret(value));
    if (field.kind === "choice") {
        return (
            <select id={id} className="kinput" value={String(value ?? "")} disabled={disabled}
                aria-describedby={helpId} aria-invalid={invalid || undefined}
                onChange={(event) => onChange(field.name, event.target.value)}>
                <option value="">Choose…</option>
                {(field.options ?? []).map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
        );
    }
    if (field.kind === "headers") return <Headers field={field} value={value} disabled={disabled} onChange={onChange} />;

    /* A masked secret is cleared the moment somebody focuses it, rather than
       left for them to select and delete. Editing around the mask is how a
       credential ends up with asterisks in the middle of it. */
    const masked = unchangedSecret(value);
    const shared = {
        id,
        value: String(value ?? ""),
        disabled,
        "aria-describedby": helpId,
        "aria-invalid": invalid || undefined,
        onChange: (event: { target: { value: string } }) => onChange(field.name, event.target.value),
    };
    if (field.kind === "secret") {
        return (
            <SecretInput
                {...shared}
                label={field.label}
                masked={masked}
                placeholder={masked ? "" : field.placeholder}
                onFocus={() => { if (masked) onChange(field.name, ""); }}
                onBlur={(event) => { if (stored && !event.target.value) onChange(field.name, REDACTED); }}
            />
        );
    }
    return (
        <input
            {...shared}
            className="kinput"
            type={field.kind === "number" ? "number" : "text"}
            placeholder={masked ? "" : field.placeholder}
            autoComplete="off"
            {...NOT_A_LOGIN}
            onFocus={() => { if (masked) onChange(field.name, ""); }}
        />
    );
}

/** A map whose keys the tenant chooses.
 *
 *  `extra_headers` on an MCP install, `default_headers` on an OpenAPI one,
 *  both described as "anything the server needs beyond the token". No fixed
 *  set of inputs can cover that, so it is pairs — and the values read back
 *  masked by position, which is what tells you a header is set without
 *  showing you what it is set to.
 */
function Headers({ field, value, disabled, onChange }: {
    field: Field;
    value: unknown;
    disabled?: boolean;
    onChange: (name: string, value: unknown) => void;
}) {
    const pairs = Object.entries((value ?? {}) as Record<string, unknown>);
    const write = (next: [string, unknown][]) => onChange(field.name, Object.fromEntries(next));
    /* The headers that were set, masked, when the form opened — by name,
       because the server restores a mask from what it holds under that name.
       A value left empty after focusing one of them means "keep it", the same
       rule a single secret box follows; without it a click in and out of the
       box blanked a real Authorization header. */
    const [stored] = useState(() => new Set(pairs.filter(([, held]) => unchangedSecret(held)).map(([key]) => key)));
    return (
        <div className="connect-pairs">
            {pairs.map(([key, held], at) => (
                <div className="connect-pair" key={at}>
                    <input
                        className="kinput kset-input"
                        aria-label="Header name"
                        placeholder="X-Example"
                        autoComplete="off"
                        {...NOT_A_LOGIN}
                        value={key}
                        disabled={disabled}
                        /* A mask cannot follow a header to a new name — the
                           server would find nothing under it and store the
                           asterisks — so renaming one asks for its value again. */
                        onChange={(event) => write(pairs.map((pair, i) => (i === at ? [event.target.value, unchangedSecret(pair[1]) ? "" : pair[1]] : pair)))}
                    />
                    <input
                        className="kinput kset-input"
                        aria-label={"Value for " + (key || "this header")}
                        type={unchangedSecret(held) ? "text" : "password"}
                        autoComplete="new-password"
                        {...NOT_A_LOGIN}
                        value={String(held ?? "")}
                        disabled={disabled}
                        onFocus={() => { if (unchangedSecret(held)) write(pairs.map((pair, i) => (i === at ? [pair[0], ""] : pair))); }}
                        onBlur={(event) => { if (!event.target.value && stored.has(key)) write(pairs.map((pair, i) => (i === at ? [pair[0], REDACTED] : pair))); }}
                        onChange={(event) => write(pairs.map((pair, i) => (i === at ? [pair[0], event.target.value] : pair)))}
                    />
                    <button type="button" className="b b--s b--quiet b--icon" aria-label={"Remove " + (key || "this header")} disabled={disabled}
                        onClick={() => write(pairs.filter((_, i) => i !== at))}>
                        <CloseIcon size={14} />
                    </button>
                </div>
            ))}
            <button type="button" className="b b--s b--secondary connect-add" disabled={disabled} onClick={() => write([...pairs, ["", ""]])}>
                <PlusIcon size={14} /> Add a header
            </button>
        </div>
    );
}
