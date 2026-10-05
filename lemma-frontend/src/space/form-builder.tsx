"use client";

import { useEffect } from "react";
import type { Pod } from "@/data";
import { draftFields, inputLabel, type FieldInput, type FormFieldDraft, type WebWidget, type WidgetDraft } from "@/data/contacts";
import { CopyButton } from "@/thread/copy-button";
import { useFormColumns, useReach } from "./contact-queries";

/** Building a form from a table: pick where answers go, tick what to ask.
 *
 *  No function, no code. Each ticked column becomes a question; a column the
 *  table can't do without is always asked. Submitting adds one row. */
export function FormBuilder({ pod, draft, set }: { pod: Pod; draft: WidgetDraft; set: (change: Partial<WidgetDraft>) => void }) {
    const reach = useReach(pod.id);
    const columns = useFormColumns(pod.id, draft.formTable);
    const tables = (reach.data?.tables ?? []).filter((table) => !table.perPerson);

    // A new table starts with every column it offers ticked.
    useEffect(() => {
        if (columns.data?.length && draft.formFields.length === 0) set({ formFields: draftFields(columns.data) });
    }, [columns.data, draft.formFields.length, set]);

    const change = (column: string, edit: Partial<FormFieldDraft>) =>
        set({ formFields: draft.formFields.map((field) => (field.column === column ? { ...field, ...edit } : field)) });

    return (
        <div className="cform">
            <label className="record-form__field">
                <span className="smanage__label">Answers go into</span>
                <select
                    value={draft.formTable}
                    onChange={(event) => set({ formTable: event.target.value, formFields: [] })}
                >
                    <option value="">Choose a table</option>
                    {tables.map((table) => <option key={table.name} value={table.name}>{table.name}</option>)}
                </select>
                <small>
                    {tables.length ? "Each answer adds a row. Responses are the table’s rows." : "Make a table first; its columns become the questions."}
                </small>
            </label>

            {draft.formTable && (
                columns.isPending ? (
                    <p className="contacts__quiet" role="status">Reading the table…</p>
                ) : columns.isError ? (
                    <p role="alert">Couldn’t read that table.</p>
                ) : draft.formFields.length === 0 ? (
                    <p className="contacts__quiet">This table has nothing a person can fill in.</p>
                ) : (
                    <fieldset className="cfields">
                        <legend className="smanage__label">Ask for</legend>
                        <ul>
                            {draft.formFields.map((field) => (
                                <li key={field.column} className={"cfield" + (field.on ? "" : " cfield--off")}>
                                    <input
                                        type="checkbox"
                                        aria-label={"Ask for " + field.column}
                                        checked={field.on}
                                        disabled={field.locked}
                                        onChange={(event) => change(field.column, { on: event.target.checked })}
                                    />
                                    <div className="cfield__main">
                                        <input
                                            className="cfield__label"
                                            aria-label={"Question for " + field.column}
                                            value={field.label}
                                            maxLength={200}
                                            disabled={!field.on}
                                            onChange={(event) => change(field.column, { label: event.target.value })}
                                        />
                                        <span className="cfield__column">{field.column}{field.locked ? " · the table needs it" : ""}</span>
                                    </div>
                                    {field.inputs.length > 1 ? (
                                        <select
                                            className="cfield__input"
                                            aria-label={"Kind of answer for " + field.column}
                                            value={field.input}
                                            disabled={!field.on}
                                            onChange={(event) => change(field.column, { input: event.target.value as FieldInput })}
                                        >
                                            {field.inputs.map((input) => <option key={input} value={input}>{inputLabel(input)}</option>)}
                                        </select>
                                    ) : (
                                        <span className="cfield__input cfield__input--fixed">{inputLabel(field.input)}</span>
                                    )}
                                    <label className="cfield__required">
                                        <input
                                            type="checkbox"
                                            aria-label={"Require " + field.column}
                                            checked={field.required || field.locked}
                                            disabled={!field.on || field.locked}
                                            onChange={(event) => change(field.column, { required: event.target.checked })}
                                        />
                                        <span>Required</span>
                                    </label>
                                </li>
                            ))}
                        </ul>
                    </fieldset>
                )
            )}

            <label className="record-form__field">
                <span className="smanage__label">Above the form</span>
                <textarea rows={2} maxLength={1000} value={draft.formIntro} onChange={(event) => set({ formIntro: event.target.value })} placeholder="Saturday, 10am. We’ll confirm by email." />
            </label>
            <label className="record-form__field">
                <span className="smanage__label">After they send it</span>
                <input maxLength={1000} value={draft.formConfirmation} onChange={(event) => set({ formConfirmation: event.target.value })} placeholder="Thanks, we’ve got it." />
            </label>
        </div>
    );
}

/** Where a widget lives: its own page to share, and the code for a website. */
export function WidgetPlaces({ widget, embed }: { widget: WebWidget; embed: string }) {
    return (
        <div className="cplaces">
            <div className="cplace">
                <span className="smanage__label">Share a link</span>
                <div className="cwidget__embed">
                    <code>{widget.pageUrl}</code>
                    <CopyButton text={widget.pageUrl} label="Copy the link" />
                </div>
                <a className="linkish" href={widget.pageUrl} target="_blank" rel="noopener noreferrer">Open the page</a>
            </div>
            <div className="cplace">
                <span className="smanage__label">Or put it on your website</span>
                <div className="cwidget__embed">
                    <code>{embed}</code>
                    <CopyButton text={embed} label="Copy the code" />
                </div>
            </div>
        </div>
    );
}
