"use client";
import { InvitationDecision } from "./invitation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "@/session/session";
import { hasApiUrl } from "@/session/client";
import { source as data } from "@/data";
import type { ActionProps } from "./action-host";
export function Actions(props: ActionProps) {
    const session = useSession();
    if (!hasApiUrl() || data.label === "sample")
        return (
            <p>
                Connect to a live workspace to continue.{" "}
                <a href="/connect">Connection settings</a>
            </p>
        );
    if (session.status === "loading")
        return <p role="status">Checking your session…</p>;
    if (session.status !== "in")
        return (
            <button className="btn btn--primary" onClick={session.signIn}>
                Sign in to continue
            </button>
        );
    return <SignedIn {...props} signOut={session.signOut} />;
}
function SignedIn(props: ActionProps & { signOut: () => Promise<void> }) {
    if (props.action === "organization") return <CreateOrganization />;
    if (props.action === "logout")
        return (
            <button className="btn" onClick={() => void props.signOut()}>
                Sign out of Lemma
            </button>
        );
    if (props.action === "invite")
        return (
            <InvitationDecision
                id={props.invitationId!}
                decision={props.decision!}
            />
        );
    return <Remix {...props} />;
}
/** Remix hands an app to a teammate in a conversation. Installing from
 *  GitHub has its own journey, in `import/install-panel.tsx`. */
function Remix({ source }: ActionProps) {
    const orgs = useQuery({
        queryKey: ["orgs"],
        queryFn: () => data.listOrgs(),
    });
    const [chosenOrg, setOrg] = useState("");
    const orgId = chosenOrg || orgs.data?.[0]?.id || "";
    const pods = useQuery({
        queryKey: ["pods", orgId],
        queryFn: () => data.listPods(orgId),
        enabled: !!orgId,
    });
    const [selectedTarget, setTarget] = useState(() =>
        new URLSearchParams(window.location.search).get("destination") ===
        "existing"
            ? "existing"
            : "new",
    );
    const target =
        selectedTarget === "existing"
            ? (pods.data?.[0]?.id ?? "new")
            : selectedTarget;
    const [name, setName] = useState("New teammate");
    const [created, setCreated] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    async function prepare() {
        setBusy(true);
        setError("");
        try {
            const url = new URL(source || "");
            if (!["http:", "https:"].includes(url.protocol))
                throw new Error("Use a valid http or https app URL.");
            let id = target === "new" ? created : target;
            if (!id) {
                const pod = await data.createPod(orgId, name.trim());
                id = pod.id;
                setCreated(id);
            }
            window.location.assign(
                "/t/" +
                    encodeURIComponent(id) +
                    "/conversation?remixSource=" +
                    encodeURIComponent(url.href),
            );
        } catch (e) {
            setError(
                e instanceof Error ? e.message : "Could not start the remix.",
            );
            setBusy(false);
        }
    }
    if (orgs.isPending) return <p role="status">Loading your organizations…</p>;
    if (orgs.isError)
        return (
            <p role="alert">
                Could not load your organizations.{" "}
                <button onClick={() => void orgs.refetch()}>Retry</button>
            </p>
        );
    if (!orgs.data?.length)
        return (
            <p>
                <a href="/organizations/new">Create an organization</a>, then
                return here to continue.
            </p>
        );
    return (
        <section className="site-card">
            <h2>Choose where to remix</h2>
            <label>
                Organization
                <select
                    value={orgId}
                    onChange={(e) => {
                        setOrg(e.target.value);
                        setTarget("new");
                        setCreated(null);
                    }}
                >
                    {orgs.data.map((o) => (
                        <option key={o.id} value={o.id}>
                            {o.name}
                        </option>
                    ))}
                </select>
            </label>
            <label>
                Teammate
                <select
                    value={target}
                    onChange={(e) => {
                        setTarget(e.target.value);
                        setCreated(null);
                    }}
                >
                    <option value="new">A new teammate</option>
                    {pods.data?.map((p) => (
                        <option key={p.id} value={p.id}>
                            {p.teammate.name}
                        </option>
                    ))}
                </select>
            </label>
            {target === "new" && (
                <label>
                    Name
                    <input
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                    />
                </label>
            )}
            <p>
                Source:{" "}
                {source || "Missing — open a Remix on Lemma link from an app."}
            </p>
            <button
                className="btn btn--primary"
                disabled={busy || !orgId || !name.trim() || !source}
                onClick={() => void prepare()}
            >
                {busy ? "Preparing…" : "Continue with teammate"}
            </button>
            <p>
                The teammate picks it up in a new conversation, where you can
                steer the remix.
            </p>
            {error && (
                <p role="alert" className="site-error">
                    {error}
                </p>
            )}
        </section>
    );
}

function CreateOrganization() {
    const [name, setName] = useState("");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    async function create() {
        setBusy(true);
        try {
            const org = await data.createOrg({ name: name.trim() });
            window.location.assign("/t?org=" + encodeURIComponent(org.id));
        } catch (e) {
            setError(
                e instanceof Error
                    ? e.message
                    : "Could not create organization.",
            );
            setBusy(false);
        }
    }
    return (
        <form
            onSubmit={(e) => {
                e.preventDefault();
                void create();
            }}
        >
            <label>
                Organization name
                <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    required
                />
            </label>
            <button
                className="btn btn--primary"
                disabled={busy || !name.trim()}
            >
                {busy ? "Creating…" : "Create organization"}
            </button>
            {error && <p role="alert">{error}</p>}
        </form>
    );
}
