import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { source, type Connectable, type Pod } from "@/data";
import { Fields } from "@/connect/fields";
import { blank, fields, payload, problems, type Values } from "@/connect/schema";

export function SurfaceCredentials({ pod, entry, onDone }: { pod: Pod; entry: Connectable; onDone: () => void }) {
    const list = fields(entry.credentialSchema);
    const [values, setValues] = useState<Values>(() => blank(list));
    const [errors, setErrors] = useState<Record<string, string>>({});
    const [accountId, setAccountId] = useState<string | null>(null);
    const connect = useMutation({
        mutationFn: async () => {
            // Retain the created account on a bind failure; retry must not create another account.
            const id = accountId ?? await source.createSurfaceAccount(pod.orgId, entry, payload(list, values));
            setAccountId(id);
            setValues({});
            return source.connectAccount(pod.id, entry.platform, id);
        },
        onSuccess: onDone,
    });
    return <form className="surface-setup" onSubmit={event => {
        event.preventDefault();
        const next = accountId ? {} : problems(list, values);
        setErrors(next);
        if (!Object.keys(next).length) connect.mutate();
    }}>
        {!accountId && <Fields list={list} values={values} problems={errors} disabled={connect.isPending} onChange={(name, value) => setValues(current => ({ ...current, [name]: value }))} />}
        {accountId && <p>Account connected. Finish attaching it to this teammate.</p>}
        <button className="btn btn--primary" disabled={connect.isPending} type="submit">{connect.isPending ? "Connecting…" : accountId ? "Retry connection" : "Connect account"}</button>
        {connect.isError && <p role="alert">{connect.error.message}</p>}
    </form>;
}
