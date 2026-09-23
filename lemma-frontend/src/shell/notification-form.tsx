"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { buildSchemaFormFields, buildSchemaFormPayload, buildSchemaFormValues } from "lemma-sdk";
import type { SchemaFormField } from "lemma-sdk";
import { lemma } from "@/session/client";
import { source } from "@/data";
import { waitingSchema, waitingUiSchema } from "./notification-state";

export function NotificationForm({
    podId,
    runId,
    nodeId,
    onDone,
}: {
    podId: string;
    runId: string;
    nodeId: string;
    onDone: () => void;
}) {
    const [values, setValues] = useState<Record<string, unknown> | null>(null);
    const [problems, setProblems] = useState<Record<string, string>>({});
    const [sending, setSending] = useState(false);
    const [failed, setFailed] = useState<string | null>(null);

    const sample = source.label === "sample";
    const run = useQuery({
        queryKey: ["workflow-run", podId, runId],
        queryFn: async () => {
            /* The sample source parks a run on a form for the same reason it
               carries notifications at all: this panel is only ever looked at
               without a session. */
            if (sample) {
                const { SAMPLE_WORKFLOW_RUN } = await import("@/data/fixtures");
                return SAMPLE_WORKFLOW_RUN;
            }
            return lemma(podId).workflows.runs.get(runId, podId);
        },
        staleTime: 15_000,
    });

    const schema = useMemo(
        () => waitingSchema(run.data as Parameters<typeof waitingSchema>[0], nodeId),
        [run.data, nodeId],
    );
    /* The author's field order, which travels on the same wait as the schema.
       Dropped here, `buildSchemaFormFields` falls back to sorting labels
       alphabetically — so a form asking for terms, then payment days, then an
       email was answered in the order C, P, T. Nothing had to be fetched to
       fix it; the value was already in hand. */
    const uiSchema = useMemo(
        () => waitingUiSchema(run.data as Parameters<typeof waitingUiSchema>[0], nodeId),
        [run.data, nodeId],
    );
    const fields = useMemo(
        () => (schema ? buildSchemaFormFields(schema, uiSchema ?? undefined) : []),
        [schema, uiSchema],
    );
    /* Third argument, not second: `buildSchemaFormValues(schema, values,
       uiSchema)` — the middle one is a set of values to seed from, so passing
       the ui schema there would have handed it initial values it never had. */
    const current = values ?? (schema ? buildSchemaFormValues(schema, undefined, uiSchema ?? undefined) : {});

    if (run.isPending) return <p className="notify__quiet" role="status">Reading the form…</p>;
    if (run.isError) {
        return (
            <p className="notify__problem" role="alert">
                That form could not be read. <button className="notify__act" onClick={() => void run.refetch()}>Try again</button>
            </p>
        );
    }
    /* The run moved on. Saying so beats a form that would be refused, and it is
       the likeliest thing to have happened to an ask that sat for a while. */
    if (!schema) {
        return <p className="notify__outcome">This has already been handled — the workflow has moved on.</p>;
    }

    function set(name: string, value: unknown) {
        setValues({ ...current, [name]: value });
        setProblems(was => (was[name] ? { ...was, [name]: "" } : was));
    }

    async function submit() {
        const built = buildSchemaFormPayload(schema, current, uiSchema ?? undefined);
        if (!built.isValid) {
            /* The server checks this too. Checking here as well is not
               duplication for its own sake — it is the difference between
               naming the field that is wrong and saying "422". */
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
            await lemma(podId).workflows.runs.submitForm(runId, { inputs: built.data, node_id: nodeId }, podId);
            onDone();
        } catch (problem) {
            const status = (problem as { statusCode?: number } | null)?.statusCode;
            setFailed(
                status === 422
                    ? "The workflow is no longer waiting on this step."
                    : problem instanceof Error
                      ? problem.message
                      : "Couldn’t submit this form.",
            );
        } finally {
            setSending(false);
        }
    }

    return (
        <form
            className="notify__form"
            onSubmit={(event) => { event.preventDefault(); void submit(); }}
        >
            {fields.map((field) => (
                <Field
                    key={field.name}
                    field={field}
                    value={current[field.name]}
                    problem={problems[field.name]}
                    onChange={(value) => set(field.name, value)}
                />
            ))}
            <div className="notify__form-actions">
                <button className="btn btn--primary" type="submit" disabled={sending}>
                    {sending ? "Sending…" : "Submit"}
                </button>
            </div>
            {failed && <p className="notify__problem" role="alert">{failed}</p>}
        </form>
    );
}

/** One field, drawn by what the schema says it is.
 *
 *  Only the kinds `buildSchemaFormFields` produces. Anything it could not
 *  classify comes back as `json`, and a textarea holding raw JSON is an honest
 *  way to render a field nobody has designed a control for yet — better than
 *  dropping it and submitting an object with a hole in it.
 */
function Field({
    field,
    value,
    problem,
    onChange,
}: {
    field: SchemaFormField;
    value: unknown;
    problem?: string;
    onChange: (value: unknown) => void;
}) {
    const id = "nf-" + field.name;
    const described = field.description ? id + "-hint" : undefined;

    if (field.kind === "boolean") {
        return (
            <label className="notify__field notify__field--check">
                <input
                    type="checkbox"
                    checked={value === true}
                    onChange={(event) => onChange(event.target.checked)}
                />
                <span>{field.label}{field.required && <i aria-hidden="true"> *</i>}</span>
                {field.description && <small>{field.description}</small>}
                {problem && <em role="alert">{problem}</em>}
            </label>
        );
    }

    return (
        <div className="notify__field" data-kind={field.kind}>
            <label htmlFor={id}>
                {field.label}{field.required && <i aria-hidden="true"> *</i>}
            </label>
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
                    onChange={(event) => onChange(field.kind === "number" ? event.target.value : event.target.value)}
                />
            )}
            {problem && <em role="alert">{problem}</em>}
        </div>
    );
}
