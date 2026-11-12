import type { RecordSchemaField, SchemaFormField } from "lemma-sdk";

/** One field of a form the phone draws natively, whichever kind of form it
 *  came from: a workflow's (a JSON schema, `buildSchemaFormFields`) or a
 *  table's record (its columns, `buildRecordSchemaFields`). The SDK decides
 *  the fields and turns the values back into a payload (`buildSchemaFormPayload`,
 *  `buildRecordPayload`); this only says how each is drawn.
 *
 *  Values are the SDK's form values: strings for text, numbers and dates
 *  (`YYYY-MM-DD`, `YYYY-MM-DDTHH:mm`), booleans for a switch, JSON as text. */

export type FieldKind = "text" | "textarea" | "email" | "number" | "boolean" | "select" | "date" | "datetime" | "json" | "reference";

export interface FormField {
    name: string;
    label: string;
    description: string | null;
    required: boolean;
    kind: FieldKind;
    options: { label: string; value: string }[];
    readOnly: boolean;
    /** A link to another table's row, by that table and its key column. */
    reference: { table: string; column: string } | null;
    /** A whole number, where `kind` is "number". */
    integer: boolean;
    /** Values a text field is known to hold, offered under the box as
     *  one-tap choices; the box still takes anything. A table's `stage`
     *  stored as text still only ever holds a few things. */
    suggestions?: string[];
}

export type FormValues = Record<string, unknown>;
export type FormErrors = Record<string, string>;

export function fromSchemaFields(fields: readonly SchemaFormField[]): FormField[] {
    return fields.map((field) => ({
        name: field.name,
        label: field.label,
        description: field.description ?? null,
        required: field.required,
        kind: field.kind,
        options: field.options,
        readOnly: false,
        reference: null,
        integer: field.type === "integer",
    }));
}

export function fromRecordFields(fields: readonly RecordSchemaField[]): FormField[] {
    return fields.map((field) => ({
        name: field.name,
        label: field.label,
        description: field.column.description ?? null,
        required: field.required,
        kind: field.kind === "foreign-key" ? "reference" : field.kind === "uuid" ? "text" : field.kind,
        options: field.options.map((option) => ({ label: option, value: option })),
        readOnly: field.readOnly,
        reference: field.foreignKey ? { table: field.foreignKey.table, column: field.foreignKey.column } : null,
        integer: field.column.type === "INTEGER" || field.column.type === "SERIAL",
    }));
}

/** At most this many choices show as chips; more open a list to pick from. */
export const CHIP_LIMIT = 6;

const pad = (part: number) => String(part).padStart(2, "0");

/** The SDK's date value as a Date in local time, or null. */
export function dateOf(value: string): Date | null {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/.exec(value);
    if (!match) return null;
    const [, year, month, day, hour, minute] = match;
    return new Date(Number(year), Number(month) - 1, Number(day), Number(hour ?? 0), Number(minute ?? 0));
}

/** A Date as the SDK's form value: `YYYY-MM-DD`, or `YYYY-MM-DDTHH:mm`. */
export function valueOf(date: Date, withTime: boolean): string {
    const day = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
    return withTime ? `${day}T${pad(date.getHours())}:${pad(date.getMinutes())}` : day;
}
