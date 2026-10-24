"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { lemma } from "@/session/client";

/** Approve a machine that ran `lemma machine connect`: see what is asking,
 *  name it, and let it in. The machine claims its own secret afterwards. */
export function MachineApproval() {
    const params = new URLSearchParams(window.location.search);
    const [code, setCode] = useState(params.get("code") ?? "");
    const pod = params.get("pod");
    const [entered, setEntered] = useState(Boolean(params.get("code")));
    const pairing = useQuery({
        queryKey: ["machine-pairing", code],
        queryFn: () => lemma().machineConnections.pairing(code),
        enabled: entered && code.length >= 6,
    });
    const [name, setName] = useState("");
    const [share, setShare] = useState(Boolean(pod));
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    const [approved, setApproved] = useState<string | null>(null);
    async function approve() {
        setBusy(true);
        setError("");
        try {
            const connection = await lemma().machineConnections.pair({
                code,
                name: name.trim() || null,
                pod_id: share && pod ? pod : null,
            });
            setApproved(connection.name);
        } catch (e) {
            setError(
                e instanceof Error ? e.message : "Could not approve the machine.",
            );
        } finally {
            setBusy(false);
        }
    }
    if (!entered)
        return (
            <section className="site-card">
                <h2>Connect a machine</h2>
                <label>
                    The code your terminal shows
                    <input
                        value={code}
                        onChange={(e) => setCode(e.target.value.toUpperCase())}
                        placeholder="ABCD-EFGH-JKMN"
                    />
                </label>
                <button
                    className="btn btn--primary"
                    disabled={code.length < 6}
                    onClick={() => setEntered(true)}
                >
                    Continue
                </button>
            </section>
        );
    if (pairing.isPending) return <p role="status">Looking up the code…</p>;
    if (pairing.isError)
        return (
            <section>
                <p role="alert">
                    That code is unknown or has expired. Run{" "}
                    <code>lemma machine connect</code> again for a new one.
                </p>
                <button className="btn" onClick={() => setEntered(false)}>
                    Enter another code
                </button>
            </section>
        );
    const machine = pairing.data;
    if (approved)
        return (
            <section className="site-card">
                <h2>{machine.display_name} is connected</h2>
                <p>
                    Agents can use it as <code>mine:{approved}</code>. The
                    machine finishes setting itself up; you can close this page.
                </p>
            </section>
        );
    return (
        <section className="site-card">
            <h2>Connect {machine.display_name}?</h2>
            <p>Check that this is the code your terminal shows: <strong>{code}</strong></p>
            <dl>
                <dt>How it runs</dt>
                <dd>
                    {machine.mode === "image"
                        ? "In a Docker container on that machine."
                        : "As the person who ran the command, with their access on that machine."}
                </dd>
                <dt>Platform</dt>
                <dd>{machine.platform}</dd>
                <dt>Code expires</dt>
                <dd>{new Date(machine.expires_at).toLocaleTimeString()}</dd>
            </dl>
            <label>
                Name (refs call it <code>mine:{name.trim() || "…"}</code>)
                <input
                    value={name}
                    onChange={(e) => setName(e.target.value.toLowerCase())}
                    placeholder="laptop"
                />
            </label>
            {pod && (
                <label>
                    <input
                        type="checkbox"
                        checked={share}
                        onChange={(e) => setShare(e.target.checked)}
                    />{" "}
                    Share it into the pod this command named
                </label>
            )}
            <button
                className="btn btn--primary"
                disabled={busy || machine.approved}
                onClick={() => void approve()}
            >
                {busy ? "Connecting…" : "Connect this machine"}
            </button>
            {error && <p role="alert">{error}</p>}
        </section>
    );
}
