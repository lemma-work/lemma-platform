"use client";

import { useMemo, useState } from "react";
import { buildSchemaFormFields, buildSchemaFormPayload, buildSchemaFormValues } from "lemma-sdk";
import type { SchemaFormField } from "lemma-sdk";
import { lemma } from "@/session/client";
import { source } from "@/data";
import { sayRefusal, type WaitRow } from "./runs";

/** The form a run is stuck on, drawn from the wait already in hand.
 *
 *  Not the connector form. `src/connect/schema.ts` reads the connector
 *  catalogue's hand-written schemas — it has `secret` and `headers` kinds, a
 *  `REDACTED` rule for values the API masks on read, and no notion of a date
 *  or an email. A workflow form is a different animal: the author wrote the
 *  schema, the backend resolves it and then validates the submission against
 *  that same resolved copy (`execution/engine.py:272`). So this renders with
 *  the SDK's `buildSchemaForm*`, which is what the platform's own frontend
 *  and this app's notification inbox already use for exactly this payload —
 *  a form authored once looks and validates the same in all three.
 *
 *  The schema comes off the wait rather than out of a fetch. The inbox already
 *  holds every wait it listed, and re-reading the run per row to find a schema
 *  that arrived with the list is N requests for nothing.
 */
export function WaitForm({ podId, runId, wait, onDone }: {
    podId: string;
    runId: string;
    wait: WaitRow;
    onDone: () => void;
}) {
    const [values, setValues] = useState<Record<string, unknown> | null>(null);
    const [problems, setProblems] = useState<Record<string, string>>({});
    const [sending, setSending] = useState(false);
    const [failed, setFailed] = useState<string | null>(null);
    const sample = source.label === "sample";

    /* `ui_schema` is passed, and the notification inbox's copy of this does not
       pass it — which is a real difference, not a nicety. With no `ui:order`
       the SDK sorts fields alphabetically by label (`schema-form.js`), so a
       form whose author wrote amount, then cost centre, then notes renders as
       Amount, Cost centre, Notes only by luck of the alphabet. The form node
       resolves and stores `ui_schema` right beside `input_schema`
       (`execution/executors/form.py:49`); reading one and ignoring the other
       throws away the author's order. */
    const fields = useMemo(
        () => buildSchemaFormFields(wait.schema, wait.uiSchema),
        [wait.schema, wait.uiSchema],
    );
    const initial = useMemo(
        () => buildSchemaFormValues(wait.schema, {}, wait.uiSchema),
        [wait.schema, wait.uiSchema],
    );
    const current = values ?? initial;

    /* A form node with an empty schema is legal — a plain "I have done it"
       acknowledgement — so no fields is a submit button, not an error. */
    if (!wait.schema) {
        return <p className="wf-note">This step is waiting on a person, but it did not arrive with a form to fill in.</p>;
    }

    function set(name: string, value: unknown) {
        setValues({ ...current, [name]: value });
        setProblems((was) => (was[name] ? { ...was, [name]: "" } : was));
    }

    async function submit() {
        const built = buildSchemaFormPayload(wait.schema, current, wait.uiSchema);
        if (!built.isValid) {
            /* The server checks this too, against the same resolved schema.
               Checking here as well is the difference between naming the field
               that is wrong and printing "422". */
            setProblems(built.errors);
            return;
        }
        setSending(true);
        setFailed(null);
        try {
            if (sample) {
                setFailed("This is the sample source — connect a session to submit anything.");
                return;
            }
            /* `node_id` is the wait's own, never a value this component was
               handed loose: the backend refuses a mismatch with a 422
               (`api/schemas.py:536`) precisely so a client cannot submit
               against a step the run has already passed. */
            await lemma(podId).workflows.runs.submitForm(runId, { node_id: wait.nodeId, inputs: built.data }, podId);
            onDone();
        } catch (problem) {
            const status = (problem as { statusCode?: number } | null)?.statusCode;
            setFailed(sayRefusal(status, problem instanceof Error ? problem.message : "Couldn’t submit this form."));
        } finally {
            setSending(false);
        }
    }

    return (
        <form className="wf-form" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
            {fields.map((field) => (
                <Field
                    key={field.name}
                    field={field}
                    value={current[field.name]}
                    problem={problems[field.name]}
                    onChange={(value) => set(field.name, value)}
                />
            ))}
            <div className="wf-form__actions">
                <button className="btn btn--primary" type="submit" disabled={sending}>
                    {sending ? "Sending…" : "Submit"}
                </button>
            </div>
            {failed && <p className="wf-problem" role="alert">{failed}</p>}
        </form>
    );
}

/** One field, by what the schema says it is.
 *
 *  Only the kinds `buildSchemaFormFields` produces. Anything it could not
 *  classify comes back as `json`, and a textarea holding raw JSON is an honest
 *  way to render a field nobody has designed a control for — better than
 *  dropping it and submitting an object with a hole in it.
 */
function Field({ field, value, problem, onChange }: {
    field: SchemaFormField;
    value: unknown;
    problem?: string;
    onChange: (value: unknown) => void;
}) {
    const id = "wf-" + field.name;
    const described = field.description ? id + "-hint" : undefined;

    if (field.kind === "boolean") {
        return (
            <label className="wf-field wf-field--check">
                <input type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />
                <span>{field.label}{field.required && <i aria-hidden="true"> *</i>}</span>
                {field.description && <small>{field.description}</small>}
                {problem && <em role="alert">{problem}</em>}
            </label>
        );
    }

    return (
        <div className="wf-field" data-kind={field.kind}>
            <label htmlFor={id}>{field.label}{field.required && <i aria-hidden="true"> *</i>}</label>
            {field.description && <small id={described}>{field.description}</small>}
            {field.kind === "select" ? (
                <select id={id} aria-describedby={described} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
                    <option value="">Choose…</option>
                    {field.options.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                </select>
            ) : field.kind === "textarea" || field.kind === "json" ? (
                <textarea
                    id={id}
                    aria-describedby={described}
                    rows={field.kind === "json" ? 4 : 3}
                    value={String(value ?? "")}
                    onChange={(event) => onChange(event.target.value)}
                />
            ) : (
                <input
                    id={id}
                    aria-describedby={described}
                    type={
                        field.kind === "number" ? "number"
                        : field.kind === "date" ? "date"
                        : field.kind === "datetime" ? "datetime-local"
                        : field.kind === "email" ? "email"
                        : "text"
                    }
                    value={String(value ?? "")}
                    onChange={(event) => onChange(event.target.value)}
                />
            )}
            {problem && <em role="alert">{problem}</em>}
        </div>
    );
}
