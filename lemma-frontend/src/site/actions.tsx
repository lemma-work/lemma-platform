"use client";
import { InvitationDecision } from "./invitation";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSession } from "@/session/session";
import { hasApiUrl, lemma } from "@/session/client";
import { DeleteAccountForm } from "@/session/delete-account";
import { takeDeletionNote } from "@/session/account-deletion";
import { source as data } from "@/data";
import { afterNewOrganization, newOrganizationLink } from "@/auth/redirects";
import type { ActionProps } from "./action-host";
import { captureEvent } from "@/site/analytics/client";
export function Actions(props: ActionProps) {
    const session = useSession();
    /* Read before the session, which a deletion has just ended: the page it
       lands on has nobody signed in to ask. */
    if (props.action === "delete-account" && deletedJustNow())
        return <AccountDeleted />;
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
    if (props.action === "delete-account") return <DeleteSignedInAccount />;
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
/* Read once per document load and remembered: the note is taken when read,
   and a later render — or a development double render — must get the same
   answer as the first. */
let deletedOnArrival: boolean | null = null;
function deletedJustNow(): boolean {
    deletedOnArrival ??= takeDeletionNote(window.location.search, () => window.sessionStorage);
    return deletedOnArrival;
}
function AccountDeleted() {
    return (
        <div role="status">
            <h2>Your account has been deleted</h2>
            <p>
                You have been signed out everywhere. You can sign up again with
                the same email address whenever you like; it will be a new
                account.
            </p>
            <a className="btn" href="/">Back to Lemma</a>
        </div>
    );
}
function DeleteSignedInAccount() {
    const me = useQuery({
        queryKey: ["current-user"],
        queryFn: () => lemma().users.current(),
    });
    if (me.isPending) return <p role="status">Loading your account…</p>;
    if (me.isError)
        return (
            <p role="alert">
                Your account could not be loaded.{" "}
                <button onClick={() => void me.refetch()}>Retry</button>
            </p>
        );
    return (
        <>
            <h2>Delete {me.data.email}</h2>
            <DeleteAccountForm email={me.data.email} />
        </>
    );
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
    const [name, setName] = useState("New Lemma");
    const [created, setCreated] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState("");
    async function prepare() {
        captureEvent("import.started", { is_remix: true });
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
                "/l/" +
                    encodeURIComponent(id) +
                    "?remixSource=" +
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
                <a href={newOrganizationLink(window.location.pathname + window.location.search)}>
                    Create an organization
                </a>
                , then continue here.
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
                    <option value="new">A new Lemma</option>
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
                {busy ? "Preparing…" : "Continue"}
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
            window.location.assign(
                afterNewOrganization(org.id, window.location.search),
            );
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
