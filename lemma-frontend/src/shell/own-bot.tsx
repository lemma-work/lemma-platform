import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import type { AccountConnect, Connectable, Pod, SlackAppWorkspace } from "@/data";
import { CheckIcon, CopyIcon, ExternalIcon, RefreshIcon } from "@/ui/icons";
import { completionPath, hereWith, openAuthorization } from "@/connect/round-trip";
import { copyText } from "@/desktop/clipboard";

/** Giving a teammate a bot of its own.
 *
 *  **One Slack app is one bot user.** That single fact is why this exists: a
 *  connected account is claimable once per organization, so the second
 *  teammate to want Slack cannot share the first one's — and should not want
 *  to, because sharing would put two teammates behind one bot with one name.
 *  Reusing the account is refused on the write with a 409 naming the holder;
 *  offering the reuse and letting it fail is the version of this flow that
 *  wastes somebody's afternoon.
 *
 *  So the answer to "Slack is taken" is not an error. It is: make this one an
 *  app of its own, named after it. The manifest is served by the deployment
 *  rather than written here, so the event and callback URLs match the backend
 *  actually answering and the scopes match the code consuming the events. */

const SLACK_APPS = "https://api.slack.com/apps";

function Copyable({ text, label }: { text: string; label: string }) {
    const [copied, setCopied] = useState(false);
    return (
        <button
            className="btn"
            onClick={() => {
                copyText(text)
                    .then(() => {
                        setCopied(true);
                        window.setTimeout(() => setCopied(false), 1600);
                    })
                    .catch(() => undefined);
            }}
        >
            {copied ? <CheckIcon size={14} /> : <CopyIcon size={14} />}
            {copied ? "Copied" : label}
        </button>
    );
}

/** Where the bot comes from: Lemma makes the app when the organization has let
 *  it, and otherwise the person does, by hand. Asking for the configuration
 *  token sits between the two, because saving it once is what turns every
 *  later teammate's four steps into one click. */
export function OwnBot({
    entry,
    pod,
    onDone,
}: {
    entry: Connectable;
    pod: Pod;
    onDone: () => void;
}) {
    const [byHand, setByHand] = useState(false);
    const workspaces = useQuery({
        queryKey: ["slack-app-workspaces", pod.id],
        queryFn: () => source.slackAppWorkspaces(pod.id),
    });
    const handIt = <button className="linkish" onClick={() => setByHand(true)}>Make the app by hand instead</button>;

    if (byHand || workspaces.isError) return <ByHand entry={entry} pod={pod} onDone={onDone} />;
    if (workspaces.isPending) {
        return <p className="guided__wait" role="status"><RefreshIcon size={13} /> Checking what Lemma can do here…</p>;
    }
    /* Saving a token and creating an app are an organization owner's or
       editor's: offering the form to anyone else only ends in a refusal. */
    if (!workspaces.data.canManage) {
        return <>
            <p className="smanage__quiet">
                An owner or editor of your organization can let Lemma make Slack apps, so {pod.teammate.name}&rsquo;s
                bot is one click. Until then, make the app by hand.
            </p>
            <ByHand entry={entry} pod={pod} onDone={onDone} />
        </>;
    }
    const usable = workspaces.data.workspaces.filter((one) => !one.needsNewToken);
    if (!usable.length) {
        return <>
            <LetLemmaMakeApps
                pod={pod}
                expired={workspaces.data.workspaces.find((one) => one.needsNewToken) ?? null}
                onSaved={() => void workspaces.refetch()}
            />
            {handIt}
        </>;
    }
    return <>
        <MadeByLemma entry={entry} pod={pod} workspaces={usable} onDone={onDone} />
        {handIt}
    </>;
}

/** Once per workspace: the token that lets Lemma create apps there. Only the
 *  refresh token is asked for, since the access token beside it on Slack's
 *  page lasts hours and Lemma rotates its own. */
function LetLemmaMakeApps({
    pod,
    expired,
    onSaved,
}: {
    pod: Pod;
    expired: SlackAppWorkspace | null;
    onSaved: () => void;
}) {
    const [refreshToken, setRefreshToken] = useState("");
    const save = useMutation({
        mutationFn: () => source.saveSlackConfigurationToken(pod.id, refreshToken),
        onSuccess: () => {
            setRefreshToken("");
            onSaved();
        },
    });
    return (
        <div className="ownbot">
            {expired && (
                <p className="approval__error">
                    Slack no longer accepts the token saved for {expired.teamName || expired.teamId}. Generate a
                    new one and paste its refresh token.
                </p>
            )}
            <p>
                Let Lemma make Slack apps in your workspace, and {pod.teammate.name}&rsquo;s bot is one click. On
                Slack&rsquo;s app page, under <em>Your App Configuration Tokens</em>, choose <em>Generate Token</em>,
                pick the workspace, and paste the refresh token here. You do this once per workspace. The token is
                yours: Slack makes every app as you, so its owners include you.
            </p>
            <div className="ownbot__fields">
                <label>
                    <span>Refresh token</span>
                    <input
                        type="password"
                        value={refreshToken}
                        autoComplete="off"
                        placeholder="xoxe-1-…"
                        onChange={(event) => setRefreshToken(event.target.value)}
                    />
                </label>
            </div>
            <div className="ownbot__acts">
                <a className="btn" href={SLACK_APPS} target="_blank" rel="noreferrer">
                    Open Slack apps <ExternalIcon size={13} />
                </a>
                <button
                    className="btn btn--primary"
                    disabled={!refreshToken.trim() || save.isPending}
                    onClick={() => save.mutate()}
                >
                    {save.isPending ? "Checking with Slack…" : "Save"}
                </button>
            </div>
            {save.isError && <p className="approval__error">{save.error.message}</p>}
        </div>
    );
}

/** Lemma creates the app and registers it; the person authorises it. */
function MadeByLemma({
    entry,
    pod,
    workspaces,
    onDone,
}: {
    entry: Connectable;
    pod: Pod;
    workspaces: SlackAppWorkspace[];
    onDone: () => void;
}) {
    const [teamId, setTeamId] = useState(workspaces[0].teamId);
    /* One key per sheet: a retry after a failed authorisation start, or a
       double click, answers with the app the first try made. */
    const [attempt] = useState(() => crypto.randomUUID());
    const [made, setMade] = useState<{ appId: string; authorization: AccountConnect } | null>(null);
    const [error, setError] = useState<string | null>(null);
    const where = (one: SlackAppWorkspace) => one.teamName || one.teamId;
    const chosen = workspaces.find((one) => one.teamId === teamId) ?? workspaces[0];
    const queryClient = useQueryClient();

    const create = useMutation({
        mutationFn: async () => {
            const app = await source.createSlackApp(pod.id, teamId, `${attempt}:${teamId}`);
            const authorization = await source.startAccount(
                pod.orgId, entry.connectorId, app.authConfigId, completionPath(hereWith({ reach: "1" })),
            );
            return { appId: app.appId, authorization };
        },
        onSuccess: (result) => {
            setMade(result);
            if (!result.authorization.authorizeUrl) setError("Made, but this deployment cannot start the authorisation.");
        },
        onError: (problem) => {
            setError(problem instanceof Error ? problem.message : "Slack did not make the app.");
            /* A token Slack refused marks its workspace: read the list again so
               the sheet asks for a new one instead of offering this again. */
            void queryClient.invalidateQueries({ queryKey: ["slack-app-workspaces", pod.id] });
        },
    });

    return (
        <div className="ownbot">
            <ol className="ownbot__steps">
                <li data-done={made ? "" : undefined} data-now={made ? undefined : ""}>
                    <b>Create {pod.teammate.name}&rsquo;s app in {where(chosen)}</b>
                    <p>Lemma makes it from the manifest, names it {pod.teammate.name}, and keeps its credentials.</p>
                    {!made && (
                        <div className="ownbot__acts">
                            {workspaces.length > 1 && (
                                <select value={teamId} onChange={(event) => setTeamId(event.target.value)}>
                                    {workspaces.map((one) => <option key={one.teamId} value={one.teamId}>{where(one)}</option>)}
                                </select>
                            )}
                            <button
                                className="btn btn--primary"
                                disabled={create.isPending}
                                onClick={() => { setError(null); create.mutate(); }}
                            >
                                {create.isPending ? "Creating…" : "Create the app"}
                            </button>
                        </div>
                    )}
                </li>
                <li data-now={made ? "" : undefined}>
                    <b>Authorise it</b>
                    <p>The one step only you can take, and the one that hands {pod.name} the bot.</p>
                    {made?.authorization.authorizeUrl && (
                        <Authorise
                            entry={entry}
                            pod={pod}
                            authorization={made.authorization}
                            onDone={onDone}
                            onError={setError}
                        />
                    )}
                    {made && (
                        <p className="smanage__quiet">
                            Slack takes the bot&rsquo;s picture only on its own page.{" "}
                            <a className="linkish" href={`${SLACK_APPS}/${made.appId}/general`} target="_blank" rel="noreferrer">
                                Add {pod.teammate.name}&rsquo;s picture <ExternalIcon size={12} />
                            </a>
                        </p>
                    )}
                </li>
            </ol>
            {error && <p className="approval__error">{error}</p>}
        </div>
    );
}

/** Open the provider's consent page, then bind the account it makes. */
function Authorise({
    entry,
    pod,
    authorization,
    onDone,
    onError,
}: {
    entry: Connectable;
    pod: Pod;
    authorization: AccountConnect;
    onDone: () => void;
    onError: (message: string) => void;
}) {
    const finish = useMutation({
        mutationFn: async () => {
            const id = await source.findAccount(pod.orgId, entry.connectorId, authorization.before, authorization.authConfigId);
            if (!id) throw new Error("Authorization is not complete yet. Finish in Slack, then check again.");
            await source.connectOwnAccount(pod.id, entry.platform, id);
        },
        onSuccess: onDone,
        onError: (problem) => onError(problem.message),
    });
    return (
        <div className="ownbot__acts">
            <button
                className="btn btn--primary"
                /* With an opener, so the finished tab reports
                   back and closes instead of reloading the app. */
                onClick={() => openAuthorization(authorization.authorizeUrl, entry.title)}
            >
                Authorise <ExternalIcon size={13} />
            </button>
            <button className="btn" disabled={finish.isPending} onClick={() => finish.mutate()}>
                <RefreshIcon size={13} /> {finish.isPending ? "Connecting…" : "Finish connection"}
            </button>
        </div>
    );
}

/** The app made by hand: copy the manifest, create it, paste its credentials,
 *  authorise it. What every Slack bot took before Lemma could make the app. */
function ByHand({
    entry,
    pod,
    onDone,
}: {
    entry: Connectable;
    pod: Pod;
    onDone: () => void;
}) {
    const [step, setStep] = useState(0);
    const [clientId, setClientId] = useState("");
    const [secret, setSecret] = useState("");
    const [signingSecret, setSigningSecret] = useState("");
    const [authorization, setAuthorization] = useState<AccountConnect | null>(null);
    const [installId, setInstallId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const manifest = useQuery({
        queryKey: ["slack-manifest", pod.teammate.name],
        queryFn: () => source.slackManifest(pod.teammate.name),
    });

    const register = useMutation({
        mutationFn: async () => {
            const authConfigId = installId ?? await source.addCustomApp(pod.orgId, entry.connectorId, pod.name + " bot", {
                client_id: clientId.trim(),
                client_secret: secret.trim(),
                signing_secret: signingSecret.trim(),
            }, entry.kind);
            setInstallId(authConfigId);
            setSecret("");
            setSigningSecret("");
            return source.startAccount(
                pod.orgId, entry.connectorId, authConfigId, completionPath(hereWith({ reach: "1" })),
            );
        },
        onSuccess: (started) => {
            setAuthorization(started);
            setStep(3);
            if (!started.authorizeUrl) setError("Registered, but this deployment cannot start the authorisation.");
        },
        onError: (problem) =>
            setError(problem instanceof Error ? problem.message : "Those credentials were not accepted."),
    });

    const text = manifest.data ? JSON.stringify(manifest.data, null, 2) : "";

    return (
        <div className="ownbot">
            <ol className="ownbot__steps">
                <li data-done={step > 0 ? "" : undefined} data-now={step === 0 ? "" : undefined}>
                    <b>Copy the manifest</b>
                    <p>
                        It already carries this deployment&rsquo;s URLs and the scopes the backend expects, and it
                        names the bot <em>{pod.teammate.name}</em>.
                    </p>
                    {step === 0 && (
                        <div className="ownbot__acts">
                            {manifest.isPending && <span className="guided__wait"><RefreshIcon size={13} /> Fetching…</span>}
                            {manifest.isError && <span className="reachrow__error">The manifest could not be read.</span>}
                            {text && <Copyable text={text} label="Copy manifest" />}
                            <button className="btn btn--primary" disabled={!text} onClick={() => setStep(1)}>
                                Next
                            </button>
                        </div>
                    )}
                </li>

                <li data-done={step > 1 ? "" : undefined} data-now={step === 1 ? "" : undefined}>
                    <b>Create the app in Slack</b>
                    <p>
                        On Slack&rsquo;s app page choose <em>Create New App</em> &rarr; <em>From a manifest</em>, pick
                        your workspace, and paste it. Then install it to the workspace.
                    </p>
                    {step === 1 && (
                        <div className="ownbot__acts">
                            <a className="btn btn--primary" href={SLACK_APPS} target="_blank" rel="noreferrer">
                                Open Slack apps <ExternalIcon size={13} />
                            </a>
                            <button className="linkish" onClick={() => setStep(2)}>Done that</button>
                        </div>
                    )}
                </li>

                <li data-done={step > 2 ? "" : undefined} data-now={step === 2 ? "" : undefined}>
                    <b>Paste the app&rsquo;s credentials</b>
                    <p>
                        From the app&rsquo;s <em>Basic Information</em> page. These are the org&rsquo;s own, so the
                        authorisation runs against your app rather than Lemma&rsquo;s.
                    </p>
                    {step === 2 && (
                        <>
                            <div className="ownbot__fields">
                                <label>
                                    <span>Client ID</span>
                                    <input
                                        value={clientId}
                                        autoComplete="off"
                                        onChange={(event) => setClientId(event.target.value)}
                                    />
                                </label>
                                <label>
                                    <span>Client secret</span>
                                    <input
                                        type="password"
                                        value={secret}
                                        autoComplete="off"
                                        onChange={(event) => setSecret(event.target.value)}
                                    />
                                </label>
                                <label>
                                    <span>Signing secret</span>
                                    <input type="password" autoComplete="off" value={signingSecret} onChange={event => setSigningSecret(event.target.value)} />
                                </label>
                            </div>
                            <div className="ownbot__acts">
                                <button
                                    className="btn btn--primary"
                                    disabled={(!installId && (!clientId.trim() || !secret.trim() || !signingSecret.trim())) || register.isPending}
                                    onClick={() => {
                                        setError(null);
                                        register.mutate();
                                    }}
                                >
                                    {register.isPending ? "Registering…" : "Register the app"}
                                </button>
                            </div>
                        </>
                    )}
                </li>

                <li data-now={step === 3 ? "" : undefined}>
                    <b>Authorise it</b>
                    <p>The last step, and the one that hands {pod.name} the bot.</p>
                    {step === 3 && authorization?.authorizeUrl && (
                        <Authorise entry={entry} pod={pod} authorization={authorization} onDone={onDone} onError={setError} />
                    )}
                </li>
            </ol>

            {error && <p className="approval__error">{error}</p>}
        </div>
    );
}
