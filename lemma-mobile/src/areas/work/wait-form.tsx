import { useMemo, useState } from "react";
import { KeyboardAvoidingView, Platform, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { buildSchemaFormFields, buildSchemaFormPayload, buildSchemaFormValues } from "lemma-sdk";
import { Loading } from "../../ui/chrome";
import { Form } from "../../ui/form/form";
import { fromSchemaFields, type FormErrors, type FormValues } from "../../ui/form/model";
import { Button } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Counted, Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { useRun, useSubmitForm } from "./hooks";
import { Note, Trouble } from "./parts";
import { workRoutes } from "./routes";
import { sayRefusal, type WaitRow } from "@web/workflow/runs";
import { humanizeName } from "@web/schedule/schedules";

/** The form a run is stuck on, drawn from the wait in hand
 *  (`workflow/form.tsx`). The SDK decides the fields, in the author's order
 *  (`ui_schema` is passed: without its `ui:order` the fields come out in the
 *  alphabet's), and builds what is sent; the phone draws it with the app's
 *  own form. A form with no fields is a plain "done": a Submit and nothing
 *  else. */
export function WaitForm({ podId, runId, wait, onDone, submitLabel = "Submit" }: {
    podId: string;
    runId: string;
    wait: WaitRow;
    onDone?: () => void;
    submitLabel?: string;
}) {
    const submit = useSubmitForm(podId, runId);
    const fields = useMemo(() => fromSchemaFields(buildSchemaFormFields(wait.schema, wait.uiSchema)), [wait.schema, wait.uiSchema]);
    const initial = useMemo(() => buildSchemaFormValues(wait.schema, {}, wait.uiSchema) as FormValues, [wait.schema, wait.uiSchema]);
    const [edited, setEdited] = useState<FormValues | null>(null);
    const [errors, setErrors] = useState<FormErrors>({});
    const [refused, setRefused] = useState<string | null>(null);
    const values = edited ?? initial;

    if (!wait.schema) return <Note>This step is waiting on a person, but it did not arrive with a form to fill in.</Note>;

    const send = () => {
        const built = buildSchemaFormPayload(wait.schema, values, wait.uiSchema);
        if (!built.isValid) {
            /* The server checks too, against the same schema; checking here
               names the field that is wrong rather than saying "422". */
            setErrors(built.errors);
            return;
        }
        setRefused(null);
        submit.mutate({ nodeId: wait.nodeId, inputs: built.data }, {
            onSuccess: () => onDone?.(),
            onError: (problem) => setRefused(sayRefusal((problem as { statusCode?: number } | null)?.statusCode, problem instanceof Error ? problem.message : "Couldn’t send this form.")),
        });
    };

    return (
        <View style={{ gap: 16 }}>
            {fields.length > 0 ? (
                <Form
                    fields={fields}
                    values={values}
                    errors={errors}
                    onChange={(name, value) => {
                        setEdited({ ...values, [name]: value });
                        if (errors[name]) setErrors((was) => { const next = { ...was }; delete next[name]; return next; });
                    }}
                />
            ) : null}
            {refused ? <Trouble text={refused} /> : null}
            <View style={{ flexDirection: "row" }}>
                <Button title={submit.isPending ? "Sending…" : submitLabel} grow height={46} busy={submit.isPending} onPress={send} />
            </View>
        </View>
    );
}

/** A form answered where it was found (Needs you): the run is read for its
 *  wait, the form drawn in a sheet, and "Open the run" a tap away. */
export function FormSheet({ podId, runId, title, workflow, open, onClose, onSent }: {
    podId: string;
    runId: string;
    title: string;
    workflow: string | null;
    open: boolean;
    onClose: () => void;
    onSent: () => void;
}) {
    const router = useRouter();
    const run = useRun(podId, open ? runId : "");
    const wait = run.data?.wait ?? null;
    const human = wait?.type === "HUMAN" ? wait : null;
    const openRun = () => { onClose(); setTimeout(() => router.push(workRoutes.run(podId, runId) as never), 300); };
    return (
        <Sheet open={open} onClose={onClose} tall title={workflow ? humanizeName(workflow) : undefined}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 12, gap: 14 }}>
                    <Counted size={18} style={{ lineHeight: 24 }}>{title}</Counted>
                    {run.isPending ? <Loading label="Reading the form" /> : run.isError ? (
                        <Trouble text="This form could not be read just now." onRetry={() => void run.refetch()} />
                    ) : !human ? (
                        <Note>{run.data ? "This run is no longer waiting on a form. Somebody got there first, or it moved on." : "This run can’t be read. It may have been removed."}</Note>
                    ) : (
                        <WaitForm podId={podId} runId={runId} wait={human} onDone={onSent} />
                    )}
                    <Button title="Open the run" kind="outline" onPress={openRun} />
                    <Text size={12.5} tone={color.ink3}>The run carries on as soon as this is sent.</Text>
                </ScrollView>
            </KeyboardAvoidingView>
        </Sheet>
    );
}
