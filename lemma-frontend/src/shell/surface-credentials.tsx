import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { createThenBind, source, type Connectable, type Pod } from "@/data";
import { SchemaFields } from "@/connect/fields";
import { blank, fields, payload, problems, type Values } from "@/connect/schema";
import { ProblemHere } from "@/connect/connection-problem";

/** A credential typed here, made into an account, shared into the pod as a
 *  SHARED connector and bound to this teammate by that connector's slug.
 *
 *  A refused bind deletes the account again (`createThenBind` has why). The
 *  values stay in the form, so trying again is one click and makes one
 *  account. */
export function SurfaceCredentials({ pod, entry, onDone }: { pod: Pod; entry: Connectable; onDone: () => void }) {
    const list = fields(entry.credentialSchema);
    const [values, setValues] = useState<Values>(() => blank(list));
    const [errors, setErrors] = useState<Record<string, string>>({});
    const connect = useMutation({
        mutationFn: () => createThenBind(
            () => source.createSurfaceAccount(pod.orgId, entry, payload(list, values)),
            (id) => source.connectOwnAccount(pod.id, entry.platform, id),
            (id) => source.disconnectAccount(pod.orgId, id),
        ),
        onSuccess: () => {
            setValues({});
            onDone();
        },
    });
    return <form className="kform" onSubmit={event => {
        event.preventDefault();
        const next = problems(list, values);
        setErrors(next);
        if (!Object.keys(next).length) connect.mutate();
    }}>
        <SchemaFields list={list} values={values} problems={errors} disabled={connect.isPending} onChange={(name, value) => setValues(current => ({ ...current, [name]: value }))} />
        {connect.isError && <ProblemHere error={connect.error} fallback="That could not be connected." />}
        <div className="kform__acts">
            <button className="b b--m b--primary" disabled={connect.isPending} type="submit">{connect.isPending ? "Connecting…" : "Connect"}</button>
        </div>
    </form>;
}
