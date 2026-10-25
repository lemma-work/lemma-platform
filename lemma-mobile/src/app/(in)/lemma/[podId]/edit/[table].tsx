import { useCallback, useMemo, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";
import { Stack, useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useQueryClient } from "@tanstack/react-query";
import { buildRecordFormValues, buildRecordPayload, buildRecordSchemaFields } from "lemma-sdk";
import { useCacheKey } from "../../../../../data/hooks";
import { Loading, Problem as Failed } from "../../../../../ui/chrome";
import { Form, type LoadReferences } from "../../../../../ui/form/form";
import { fromRecordFields, type FormErrors, type FormValues } from "../../../../../ui/form/model";
import { Button, Press } from "../../../../../ui/press";
import { Text } from "../../../../../ui/text";
import { color, TOUCH } from "../../../../../ui/tokens";
import { Problem, Quiet } from "../../../../../areas/tables/bits";
import { patchSaved, useRecord, useRowSample, useShape, useTablesSource } from "../../../../../areas/tables/hooks";
import { matching, NO_FILTERS } from "@web/library/filters";
import { profileTable } from "@web/library/profile";
import { readableName } from "@web/library/reading";
import { rowLabel, type Row } from "@web/library/record-cache";

/** Making a row, or changing one (`lemma-frontend` `RecordEditor`).
 *
 *  The SDK decides the form: which columns a person may fill
 *  (`buildRecordSchemaFields`, without the read-only ones), what is in each
 *  to start with (`buildRecordFormValues`), and what goes back, checked
 *  against the column types (`buildRecordPayload`). The phone only draws it.
 *  A link to another table's row is picked from that table's rows; a text
 *  column that only ever holds a few values offers them. */
export default function EditRow() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; table: string; id?: string; preset?: string }>();
    const podId = String(params.podId);
    const tableName = String(params.table);
    const recordId = params.id ? String(params.id) : null;
    const preset = useMemo<Row>(() => {
        try { return params.preset ? JSON.parse(String(params.preset)) as Row : {}; } catch { return {}; }
    }, [params.preset]);
    const source = useTablesSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const shape = useShape(podId, tableName);
    const record = useRecord(podId, tableName, recordId ?? "");
    const sample = useRowSample(podId, tableName);
    const table = shape.data ?? null;
    const primaryKey = table?.primary_key_column ?? "id";

    const known = useMemo(() => {
        if (!sample.data?.length) return new Map<string, string[]>();
        const profile = profileTable(table?.columns ?? [], sample.data, { primaryKey });
        return new Map(profile.columns.filter((column) => column.role === "enum").map((column) => [column.name, column.values] as const));
    }, [sample.data, table, primaryKey]);
    const fields = useMemo(() => {
        if (!table) return [];
        return fromRecordFields(buildRecordSchemaFields(table).filter((field) => !field.readOnly)).map((field) => (
            (field.kind === "text" || field.kind === "textarea") && known.get(field.name)?.length ? { ...field, suggestions: known.get(field.name) } : field
        ));
    }, [table, known]);

    const ready = Boolean(table) && (!recordId || Boolean(record.data));
    const baseline = useMemo<FormValues | null>(() => {
        if (!table || !ready) return null;
        return buildRecordFormValues(table, recordId ? record.data ?? {} : preset);
    }, [table, ready, recordId, record.data, preset]);
    const [edited, setEdited] = useState<FormValues | null>(null);
    const values = edited ?? baseline ?? {};
    const [errors, setErrors] = useState<FormErrors>({});
    const [problem, setProblem] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const dirty = edited !== null && JSON.stringify(edited) !== JSON.stringify(baseline);

    const loadReferences = useCallback<LoadReferences>(async (reference, query) => {
        const rows = await cache.fetchQuery({
            queryKey: [key, "tables", podId, reference.table, "linkable"],
            queryFn: () => source.linkable(podId, reference.table),
            staleTime: 60_000,
        });
        const found = query.trim() ? matching(rows, { ...NO_FILTERS, text: query }) : rows;
        return found.slice(0, 60).flatMap((row) => {
            const value = row[reference.column];
            if (value === null || value === undefined) return [];
            return [{ label: rowLabel(row, reference.column), value: String(value) }];
        });
    }, [cache, key, podId, source]);

    const save = async () => {
        if (!table) return;
        const mode = recordId ? "update" : "create";
        const payload = buildRecordPayload(table, values, { mode });
        setErrors(payload.errors);
        if (!payload.isValid) { setProblem("Some fields need a look."); return; }
        setSaving(true);
        setProblem(null);
        try {
            const saved = recordId ? await source.update(podId, tableName, recordId, payload.data) : await source.create(podId, tableName, payload.data);
            patchSaved(cache, key, podId, tableName, primaryKey, saved, mode, recordId ?? undefined);
            router.back();
        } catch (failure) {
            setProblem(failure instanceof Error && failure.message ? failure.message : "That was not saved. Try again.");
        } finally {
            setSaving(false);
        }
    };

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <Stack.Screen options={{ animation: "slide_from_bottom" }} />
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingHorizontal: 8 }}>
                    <Press label="Cancel" onPress={() => router.back()} style={{ height: TOUCH, justifyContent: "center", paddingHorizontal: 10 }}>
                        <Text size={16}>Cancel</Text>
                    </Press>
                </View>
                <View style={{ paddingHorizontal: 20, paddingBottom: 6, gap: 2 }}>
                    <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>{recordId ? "Edit row" : "New row"}</Text>
                    <Text size={14} tone={color.ink2}>{readableName(tableName)}{recordId && record.data ? ` · ${rowLabel(record.data, primaryKey)}` : ""}</Text>
                </View>
                {shape.isError || record.isError ? (
                    <Failed error={new Error("This table could not be read.")} onRetry={() => { void shape.refetch(); if (recordId) void record.refetch(); }} />
                ) : !ready ? (
                    <Loading label="Reading the table" />
                ) : fields.length === 0 ? (
                    <Text size={15.5} tone={color.ink2} style={{ paddingHorizontal: 20, paddingTop: 12, lineHeight: 22 }}>Nothing in this table is filled in by hand.</Text>
                ) : (
                    <>
                        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: 24 }}>
                            <Form
                                fields={fields}
                                values={values}
                                errors={errors}
                                loadReferences={loadReferences}
                                onChange={(name, value) => {
                                    setEdited({ ...values, [name]: value });
                                    if (errors[name]) setErrors((was) => { const next = { ...was }; delete next[name]; return next; });
                                }}
                            />
                            {problem ? <Problem text={problem} onDismiss={() => setProblem(null)} /> : null}
                        </ScrollView>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8, borderTopWidth: 1, borderTopColor: color.line }}>
                            {recordId && dirty ? <Quiet text="Undo my edits" onPress={() => { setEdited(null); setErrors({}); setProblem(null); }} /> : null}
                            <Button title={saving ? "Saving…" : recordId ? "Save changes" : "Create row"} grow height={46} busy={saving} disabled={Boolean(recordId) && !dirty} onPress={() => void save()} />
                        </View>
                    </>
                )}
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
