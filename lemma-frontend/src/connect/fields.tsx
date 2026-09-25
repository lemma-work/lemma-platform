import { useState } from "react";
import { CloseIcon, PlusIcon } from "@/ui/icons";
import { REDACTED, unchangedSecret, type Field, type Values } from "./schema";

/** A server-described form, drawn.
 *
 *  Borrows the record editor's shape, because it is the same thing wearing a
 *  different schema: a field list the server owns, rendered rather than
 *  hand-written per connector.
 */
export function Fields({ list, values, problems, onChange, disabled }: {
    list: Field[];
    values: Values;
    problems: Record<string, string>;
    onChange: (name: string, value: unknown) => void;
    disabled?: boolean;
}) {
    return (
        <div className="record-form">
            {list.map((field) => (
                <div className="record-form__field" key={field.name}>
                    {/* A checkbox carries its own label, so a second one above
                        it just prints the name twice. */}
                    {field.kind !== "boolean" && (
                        <label htmlFor={"f-" + field.name}>
                            {field.label}{field.required && <i aria-hidden="true"> *</i>}
                        </label>
                    )}
                    <One field={field} value={values[field.name]} disabled={disabled} onChange={onChange} />
                    {/* Under the box rather than between it and its label:
                        a connector's help runs to three lines, and above the
                        input it pushed the label a paragraph away from the
                        thing it names. */}
                    {problems[field.name] && <em role="alert">{problems[field.name]}</em>}
                    {field.description && <small id={"f-" + field.name + "-help"}>{field.description}</small>}
                </div>
            ))}
        </div>
    );
}

function One({ field, value, disabled, onChange }: {
    field: Field;
    value: unknown;
    disabled?: boolean;
    onChange: (name: string, value: unknown) => void;
}) {
    /* Whether a stored secret stood here when the form opened. Only then does
       leaving the box empty mean "keep it": on a new connection there is
       nothing stored, and putting the mask back wrote "********" into an empty
       required field — which passed the required check, was dropped from the
       payload as untouched, and reached the server as no token at all. */
    const [stored] = useState(() => unchangedSecret(value));
    if (field.kind === "boolean") {
        return (
            <label className="connect-check">
                <input
                    id={"f-" + field.name}
                    type="checkbox"
                    checked={Boolean(value)}
                    disabled={disabled}
                    onChange={(event) => onChange(field.name, event.target.checked)}
                />
                <span>{field.label}</span>
            </label>
        );
    }
    if (field.kind === "choice") {
        return (
            <select id={"f-" + field.name} value={String(value ?? "")} disabled={disabled}
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
    return (
        <input
            id={"f-" + field.name}
            aria-describedby={field.description ? "f-" + field.name + "-help" : undefined}
            type={field.kind === "secret" && !masked ? "password" : field.kind === "number" ? "number" : "text"}
            value={String(value ?? "")}
            placeholder={masked ? "" : field.placeholder}
            disabled={disabled}
            autoComplete={field.kind === "secret" ? "off" : undefined}
            spellCheck={field.kind === "secret" ? false : undefined}
            onFocus={() => { if (masked) onChange(field.name, ""); }}
            onBlur={(event) => { if (stored && field.kind === "secret" && !event.target.value) onChange(field.name, REDACTED); }}
            onChange={(event) => onChange(field.name, event.target.value)}
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
    return (
        <div className="connect-pairs">
            {pairs.map(([key, held], at) => (
                <div className="connect-pair" key={at}>
                    <input
                        aria-label="Header name"
                        placeholder="X-Example"
                        value={key}
                        disabled={disabled}
                        onChange={(event) => write(pairs.map((pair, i) => (i === at ? [event.target.value, pair[1]] : pair)))}
                    />
                    <input
                        aria-label={"Value for " + (key || "this header")}
                        type={unchangedSecret(held) ? "text" : "password"}
                        value={String(held ?? "")}
                        disabled={disabled}
                        onFocus={() => { if (unchangedSecret(held)) write(pairs.map((pair, i) => (i === at ? [pair[0], ""] : pair))); }}
                        onChange={(event) => write(pairs.map((pair, i) => (i === at ? [pair[0], event.target.value] : pair)))}
                    />
                    <button type="button" aria-label={"Remove " + (key || "this header")} disabled={disabled}
                        onClick={() => write(pairs.filter((_, i) => i !== at))}>
                        <CloseIcon size={13} />
                    </button>
                </div>
            ))}
            <button type="button" className="connect-add" disabled={disabled} onClick={() => write([...pairs, ["", ""]])}>
                <PlusIcon size={13} /> Add a header
            </button>
        </div>
    );
}
