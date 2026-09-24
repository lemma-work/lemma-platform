import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import type { AccountConnect, Connectable, Pod } from "@/data";
import { CheckIcon, CopyIcon, ExternalIcon, RefreshIcon } from "@/ui/icons";
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

export function OwnBot({
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
    const [accountId, setAccountId] = useState<string | null>(null);
    const authorizeUrl = authorization?.authorizeUrl;
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
            return source.startAccount(pod.orgId, entry.connectorId, authConfigId);
        },
        onSuccess: (started) => {
            setAuthorization(started);
            setStep(3);
            if (!started.authorizeUrl) setError("Registered, but this deployment cannot start the authorisation.");
        },
        onError: (problem) =>
            setError(problem instanceof Error ? problem.message : "Those credentials were not accepted."),
    });

    const finish = useMutation({
        mutationFn: async () => {
            if (!authorization) throw new Error("Start authorization first.");
            const id = accountId ?? await source.findAccount(pod.orgId, entry.connectorId, authorization.before, authorization.authConfigId);
            if (!id) throw new Error("Authorization is not complete yet. Finish in Slack, then check again.");
            setAccountId(id);
            await source.connectAccount(pod.id, entry.platform, id);
        },
        onSuccess: onDone,
        onError: problem => setError(problem.message),
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
                    {step === 3 && authorizeUrl && (
                        <div className="ownbot__acts">
                            <a
                                className="btn btn--primary"
                                href={authorizeUrl}
                                target="_blank"
                                rel="noreferrer"
                            >
                                Authorise <ExternalIcon size={13} />
                            </a>
                            <button className="btn" disabled={finish.isPending} onClick={() => { setError(null); finish.mutate(); }}>
                                <RefreshIcon size={13} /> {finish.isPending ? "Connecting…" : "Finish connection"}
                            </button>
                        </div>
                    )}
                </li>
            </ol>

            {error && <p className="approval__error">{error}</p>}
        </div>
    );
}
