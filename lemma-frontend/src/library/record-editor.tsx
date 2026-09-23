"use client";

import { useMemo } from "react";
import { useRecordForm } from "lemma-sdk/react";
import type { RecordSchemaField } from "lemma-sdk";
import { lemma } from "@/session/client";
import { Modal } from "@/shell/modal";
import type { Row } from "./record-cache";
import type { TableProfile } from "./profile";
import { readableName } from "./reading";

/** Making and changing a row.
 *
 *  The form is the SDK's, not this app's: `useRecordForm` reads the table's own
 *  schema, decides which fields a person may fill, validates against the column
 *  types, tracks what has actually changed, and picks create or update by
 *  whether it was handed a record id. Writing any of that here would be a
 *  second implementation of rules the server already owns, and the two would
 *  drift in the direction of whichever was edited last.
 *
 *  What is left is deciding what a field looks like, which is this app's job
 *  and nobody else's.
 */
export function RecordEditor({
    podId,
    tableName,
    primaryKey,
    row,
    preset,
    profile,
    onSaved,
    onClose,
}: {
    podId: string;
    tableName: string;
    primaryKey: string;
    /** The row being changed, or null to make one. */
    row: Row | null;
    /** What the place it was added from already implies — added under
     *  "Trialling", a new row starts out trialling. */
    preset?: Row;
    /** What the rows themselves say about the columns.
     *
     *  The schema decides a column's *type*; it does not know that a `stage`
     *  stored as text only ever holds four things. The table does, in its own
     *  rows, and a person changing a stage wants the four rather than a box to
     *  retype one of them into. */
    profile?: TableProfile | null;
    onSaved: (record: Row, mode: "create" | "update") => void;
    onClose: () => void;
}) {
    const client = useMemo(() => lemma(podId), [podId]);
    const recordId = row ? String(row[primaryKey] ?? "") : null;

    const form = useRecordForm({
        client,
        podId,
        tableName,
        recordId: recordId || null,
        /* The row is already in hand from the table, so the form does not need
           to fetch it again to show what is in it. */
        initialValues: row ?? (preset && Object.keys(preset).length ? preset : undefined),
        mode: recordId ? "update" : "create",
        onSubmitSuccess: (record) => {
            onSaved(record as Row, recordId ? "update" : "create");
            onClose();
        },
    });

    const fields = form.editableFields;

    return (
        <Modal
            title={recordId ? "Edit row" : "New row"}
            subtitle={readableName(tableName)}
            onClose={onClose}
        >
            {form.isLoadingSchema ? (
                <p role="status">Reading the table…</p>
            ) : fields.length === 0 ? (
                /* Every column is generated, computed or system-owned. Saying
                   so beats an empty dialog with a Save button under it. */
                <p role="status">Nothing in this table is filled in by hand.</p>
            ) : (
                <form
                    className="record-form"
                    onSubmit={(event) => { event.preventDefault(); void form.submit(); }}
                >
                    {fields.map((field) => (
                        <RecordField
                            key={field.name}
                            field={field}
                            value={form.values[field.name]}
                            problem={form.fieldErrors[field.name]}
                            known={known(profile, field.name)}
                            onChange={(value) => form.setValue(field.name, value)}
                        />
                    ))}

                    {form.error && <p className="record-form__problem" role="alert">{form.error.message}</p>}

                    <div className="record-form__actions">
                        <button
                            className="btn btn--primary"
                            type="submit"
                            /* A save with nothing changed is a round trip to
                               write what is already there. */
                            disabled={form.isSubmitting || (Boolean(recordId) && !form.isDirty)}
                        >
                            {form.isSubmitting ? "Saving…" : recordId ? "Save changes" : "Create row"}
                        </button>
                        <button className="btn" type="button" onClick={onClose}>Cancel</button>
                        {recordId && form.isDirty && (
                            <button className="record-form__revert" type="button" onClick={() => form.reset()}>
                                Undo my edits
                            </button>
                        )}
                    </div>
                </form>
            )}
        </Modal>
    );
}

/** The values a column is actually known to hold, where there are few enough
 *  of them to be a list. Empty for everything else, which is most columns. */
function known(profile: TableProfile | null | undefined, column: string): string[] {
    const found = profile?.columns.find((c) => c.name === column);
    return found?.role === "enum" ? found.values : [];
}

/** One column, drawn as what its type says it is.
 *
 *  `foreign-key` and `uuid` get a plain text box on purpose. A picker for a
 *  foreign key needs the other table's rows and a way to name them, which is a
 *  real feature rather than a widget — and a box that takes the id is honest
 *  about what it wants, where a half-built picker would not be.
 */
function RecordField({
    field,
    value,
    problem,
    known,
    onChange,
}: {
    field: RecordSchemaField;
    value: unknown;
    problem?: string;
    /** Values this column is known to hold, read off the table's own rows. */
    known: string[];
    onChange: (value: unknown) => void;
}) {
    const id = "rf-" + field.name;
    const hint = field.column?.description || (field.foreignKey ? "References " + field.foreignKey.table : "");

    if (field.kind === "boolean") {
        return (
            <label className="record-form__field record-form__field--check">
                <input type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />
                <span>{field.label}{field.required && <i aria-hidden="true"> *</i>}</span>
                {hint && <small>{hint}</small>}
                {problem && <em role="alert">{problem}</em>}
            </label>
        );
    }

    return (
        <div className="record-form__field" data-kind={field.kind}>
            <label htmlFor={id}>
                {field.label}{field.required && <i aria-hidden="true"> *</i>}
            </label>
            {hint && <small>{hint}</small>}
            {field.kind === "select" ? (
                <select id={id} value={String(value ?? "")} onChange={(event) => onChange(event.target.value)}>
                    <option value="">—</option>
                    {field.options.map((option) => (
                        <option key={option} value={option}>{option}</option>
                    ))}
                </select>
            ) : known.length > 0 && (field.kind === "text" || field.kind === "textarea") ? (
                /* The schema calls it text and it is, but the table has only
                   ever held four things in it. A list of the four, and the box
                   still there, because a list read off today's rows must not be
                   the reason nobody can ever add a fifth. */
                <>
                    <input id={id} list={id + "-known"} value={String(value ?? "")}
                        onChange={(event) => onChange(event.target.value)}/>
                    <datalist id={id + "-known"}>
                        {known.map((option) => <option key={option} value={option}/>)}
                    </datalist>
                    <span className="record-form__known">
                        {known.map((option) => (
                            <button key={option} type="button" aria-pressed={String(value ?? "") === option}
                                onClick={() => onChange(option)}>{option}</button>
                        ))}
                    </span>
                </>
            ) : field.kind === "textarea" || field.kind === "json" ? (
                <textarea
                    id={id}
                    rows={field.kind === "json" ? 5 : 3}
                    value={typeof value === "string" ? value : value == null ? "" : JSON.stringify(value, null, 2)}
                    onChange={(event) => onChange(event.target.value)}
                />
            ) : (
                <input
                    id={id}
                    type={
                        field.kind === "number" ? "number"
                        : field.kind === "date" ? "date"
                        : field.kind === "datetime" ? "datetime-local"
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
