import { describe, expect, it } from "vitest";
import { buildRecordSchemaFields, buildSchemaFormFields, buildSchemaFormPayload, type Table } from "lemma-sdk";
import { dateOf, fromRecordFields, fromSchemaFields, valueOf } from "../src/ui/form/model";

describe("form fields from a workflow's schema", () => {
    const schema = {
        type: "object" as const,
        properties: {
            approved: { type: "boolean" as const, title: "Approve the refund?" },
            amount: { type: "integer" as const, title: "Amount" },
            reason: { type: "string" as const, enum: ["damaged", "late", "wrong item"] },
            on: { type: "string" as const, format: "date" },
        },
        required: ["approved"],
    };

    it("draws each property as the kind the SDK decides", () => {
        const fields = fromSchemaFields(buildSchemaFormFields(schema));
        const kinds = Object.fromEntries(fields.map((field) => [field.name, field.kind]));
        expect(kinds).toEqual({ approved: "boolean", amount: "number", reason: "select", on: "date" });
        expect(fields.find((field) => field.name === "amount")?.integer).toBe(true);
        expect(fields.find((field) => field.name === "reason")?.options.map((option) => option.value)).toEqual(["damaged", "late", "wrong item"]);
        expect(fields.find((field) => field.name === "approved")?.required).toBe(true);
    });

    it("turns the values it draws back into the SDK's payload", () => {
        const result = buildSchemaFormPayload(schema, { approved: true, amount: "180", reason: "late", on: valueOf(new Date(2026, 9, 24), false) });
        expect(result.isValid).toBe(true);
        expect(result.data).toEqual({ approved: true, amount: 180, reason: "late", on: "2026-10-24" });
    });
});

describe("form fields from a table's columns", () => {
    const table = {
        name: "tasks",
        columns: [
            { name: "id", type: "UUID" },
            { name: "title", type: "TEXT", required: true },
            { name: "estimate", type: "FLOAT" },
            { name: "status", type: "ENUM", options: ["todo", "doing", "done"] },
            { name: "project_id", type: "UUID", foreign_key: { references: "projects.id" } },
        ],
    } as unknown as Table;

    it("links a foreign key to its table and lists an enum's options", () => {
        const fields = fromRecordFields(buildRecordSchemaFields(table));
        const project = fields.find((field) => field.name === "project_id");
        expect(project?.kind).toBe("reference");
        expect(project?.reference).toEqual({ table: "projects", column: "id" });
        expect(fields.find((field) => field.name === "status")?.options.map((option) => option.label)).toEqual(["todo", "doing", "done"]);
    });
});

describe("date values", () => {
    it("reads and writes the SDK's date and date-time values in local time", () => {
        const date = dateOf("2026-10-24T09:05");
        expect(date?.getHours()).toBe(9);
        expect(valueOf(date!, true)).toBe("2026-10-24T09:05");
        expect(valueOf(dateOf("2026-01-02")!, false)).toBe("2026-01-02");
        expect(dateOf("")).toBeNull();
    });
});
