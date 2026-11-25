import { useMemo, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Button } from "../../../ui/press";
import { Copyable, Field, Group, ReadProblem, Reading, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { Fields } from "../connect/fields";
import { NEEDS_EDITOR, connectSchema, connectorProblem, installSchema, kindNamed, type Install } from "@web/connect/install";
import { blank, fields, payload, problems, type Values } from "@web/connect/schema";
import { useAddOwn, useCheckSignIn, useConnectorHere, useDetail, useMayManage, useSignIn, useSubmitConnect } from "./hooks";
import { lookOf } from "./looks";
import { installsShown } from "./model";
import { Actions, Note } from "./parts";
import { connectTarget, nameProblem, planConnect, type Added } from "./plan";
import { connectorsRoutes } from "./routes";
import { signInWords } from "./sign-in";
import type { SignInResult } from "./source";

/** How a sign-in went, and what is left to do: check again when nothing
 *  came back yet, open the account when it still needs installing. */
function SignedIn({ result, title, connectorId, checking, onCheck }: {
    result: SignInResult;
    title: string;
    connectorId: string;
    checking: boolean;
    onCheck: () => void;
}) {
    const router = useRouter();
    const said = signInWords(result, title);
    return (
        <>
            <Note tone={said.bad ? "bad" : result.kind === "connected" ? "done" : "quiet"} text={said.text} />
            <Actions>
                {result.kind === "nothing" ? <Button title={checking ? "Checking…" : "Check again"} busy={checking} onPress={onCheck} /> : null}
                {result.kind === "unfinished" ? (
                    <Button title="Open the account" onPress={() => router.replace(connectorsRoutes.account(connectorId, result.accountId) as never)} />
                ) : null}
                <Button title="Done" kind={result.kind === "connected" || result.kind === "refused" || result.kind === "no-page" ? "primary" : "outline"} onPress={() => router.back()} />
            </Actions>
        </>
    );
}

/** Connecting an account, by whichever route this connector is on (the
 *  web's `ConnectDialog`, as a screen). The organization's own OAuth app
 *  first where Lemma holds none (or when asked for, with the redirect URL
 *  to register); a credential typed in; or a sign-in through the provider in
 *  the phone's browser, which can need a question answered first. The
 *  screen ends on what happened. */
export function ConnectScreen({ connectorId, installId, own }: { connectorId: string; installId: string | null; own: boolean }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const detail = useDetail(connectorId);
    const manage = useMayManage(here.orgId);
    /* An install made here: the organization's own app, when an account
       then goes on it by a credential or a question first. */
    const [made, setMade] = useState<Install | null>(null);
    const [bringingApp, setBringingApp] = useState(own);
    const [values, setValues] = useState<Values>({});
    const [shown, setShown] = useState<Record<string, string>>({});
    const [result, setResult] = useState<SignInResult | null>(null);
    const [connected, setConnected] = useState(false);
    const submit = useSubmitConnect(here.orgId);
    const signIn = useSignIn(here.orgId);
    const check = useCheckSignIn(here.orgId);

    const title = here.connector?.title ?? detail.data?.entry.title ?? "this connector";
    const against = made ?? connectTarget(here.installs, installId);
    const { may, known } = manage;
    const plan = useMemo(
        () => (detail.data ? planConnect({ entry: detail.data.entry, title, against, bringingApp, manage: { may, known } }) : null),
        [detail.data, title, against, bringingApp, may, known],
    );
    const ready = plan && plan.list.length > 0 && Object.keys(values).length === 0 ? blank(plan.list) : values;
    const busy = submit.isPending || signIn.isPending;

    const back = here.connector?.title ?? "Connectors";
    if (here.pending || detail.isLoading) return <SettingsScreen title={`Connect ${title}`} back={back}><Reading label="Reading what this one needs" /></SettingsScreen>;
    if (detail.isError || !plan) {
        return (
            <SettingsScreen title={`Connect ${title}`} back={back}>
                <ReadProblem said={problemOf(detail.error, "What this connector needs could not be read. Try again.")} onRetry={() => void detail.refetch()} />
            </SettingsScreen>
        );
    }

    const startSignIn = (install: string | null, extra?: Record<string, unknown>) => {
        setResult(null);
        signIn.mutate({ connectorId, installId: install, fields: extra }, { onSuccess: setResult });
    };

    const go = () => {
        const found = problems(plan.list, ready);
        setShown(found);
        if (Object.keys(found).length > 0) return;
        submit.mutate({ plan, connectorId, against, takenNames: here.takenNames, values: ready }, {
            onSuccess: (step) => {
                if (step.next === "sign-in") {
                    if (step.made) { setMade(step.made); setBringingApp(false); }
                    startSignIn(step.installId, step.fields);
                }
                else if (step.next === "form") { setMade(step.made); setBringingApp(false); setValues({}); setShown({}); }
                else setConnected(true);
            },
        });
    };

    const problem = submit.error ?? signIn.error;
    const failure = problem ? (isForbidden(problem) ? NEEDS_EDITOR : connectorProblem(problem, "Couldn’t connect this account.")) : null;
    const note = against && plan.mode !== "app-form" && installsShown(detail.data!.entry, here.installs) ? `On ${against.name || against.id}` : null;

    if (connected) {
        return (
            <SettingsScreen title={plan.title} back={back}>
                <Say tone="done">Connected to {title}.</Say>
                <View style={{ flexDirection: "row" }}><Button title="Done" onPress={() => router.back()} /></View>
            </SettingsScreen>
        );
    }
    if (result) {
        return (
            <SettingsScreen title={plan.title} back={back}>
                <SignedIn
                    result={result}
                    title={title}
                    connectorId={connectorId}
                    checking={check.isPending}
                    onCheck={() => { if (result.kind === "nothing") check.mutate(result.pending, { onSuccess: setResult }); }}
                />
            </SettingsScreen>
        );
    }

    return (
        <SettingsScreen title={plan.title} note={note} back={back}>
            {plan.lead ? <Say tone={plan.mode === "undescribed" ? "bad" : "quiet"}>{plan.lead}</Say> : null}
            {plan.showRedirectUri && detail.data?.redirectUri ? (
                <Group label="Add this redirect URL to the app" foot="Without it, the first sign-in fails with a redirect mismatch.">
                    <Copyable value={detail.data.redirectUri} label="the redirect URL" mono />
                </Group>
            ) : null}

            {plan.mode === "needs-manager" || plan.mode === "undescribed" ? (
                <View style={{ flexDirection: "row" }}><Button title="Back" kind="outline" onPress={() => router.back()} /></View>
            ) : plan.mode === "redirect" || plan.mode === "nothing-to-ask" ? (
                <>
                    {failure ? <Say tone="bad">{failure}</Say> : null}
                    <Actions>
                        <Button
                            title={signIn.isPending ? `Opening ${title}…` : submit.isPending ? "Connecting…" : plan.submit}
                            busy={busy}
                            onPress={plan.mode === "redirect" ? () => startSignIn(against?.id ?? null) : go}
                        />
                        {plan.offerOwnApp ? (
                            <Button title="Use your own app" kind="outline" disabled={busy} onPress={() => { setBringingApp(true); setValues({}); setShown({}); }} />
                        ) : null}
                    </Actions>
                </>
            ) : (
                <>
                    <Fields list={plan.list} values={ready} problems={shown} disabled={busy} onChange={(field, value) => setValues({ ...ready, [field]: value })} />
                    {failure ? <Say tone="bad">{failure}</Say> : null}
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Cancel" kind="outline" grow disabled={busy} onPress={() => router.back()} />
                        <Button
                            title={signIn.isPending ? `Opening ${title}…` : busy ? "Saving…" : plan.submit}
                            grow
                            busy={busy}
                            onPress={go}
                        />
                    </View>
                </>
            )}
        </SettingsScreen>
    );
}

/** Pointing the organization at a server, an API or a database of its own
 *  (the web's `AddConnector`, with the kind already picked). The first
 *  question is what kind of thing it is, which the door it came through
 *  answered; the entry follows from that. The address and the token are
 *  asked in one sitting, then its operations are read, and what that found
 *  is said, because "connected, and it advertised nothing" is the answer
 *  somebody most needs to read. */
export function AddOwnScreen({ connectorId, kind }: { connectorId: string; kind: string }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const detail = useDetail(connectorId);
    const manage = useMayManage(here.orgId);
    const spec = useMemo(() => (detail.data ? kindNamed(detail.data.entry, kind) : null), [detail.data, kind]);
    const list = useMemo(() => fields(installSchema(spec)), [spec]);
    const secrets = useMemo(() => fields(connectSchema(spec)), [spec]);
    const [name, setName] = useState("");
    const [values, setValues] = useState<Values>({});
    const [creds, setCreds] = useState<Values>({});
    const [shown, setShown] = useState<Record<string, string>>({});
    const [failure, setFailure] = useState<string | null>(null);
    const [added, setAdded] = useState<Added | null>(null);
    const [result, setResult] = useState<SignInResult | null>(null);
    const add = useAddOwn(here.orgId);
    const signIn = useSignIn(here.orgId);
    const check = useCheckSignIn(here.orgId);

    const look = lookOf(kind);
    const title = look?.title ?? here.connector?.title ?? "Add your own";
    const ready = list.length > 0 && Object.keys(values).length === 0 ? blank(list) : values;
    const readyCreds = secrets.length > 0 && Object.keys(creds).length === 0 ? blank(secrets) : creds;

    if (here.pending || detail.isLoading) return <SettingsScreen title={title}><Reading label="Reading what this needs" /></SettingsScreen>;
    if (detail.isError || !detail.data) {
        return <SettingsScreen title={title}><ReadProblem said={problemOf(detail.error, "What this needs could not be read. Try again.")} onRetry={() => void detail.refetch()} /></SettingsScreen>;
    }
    if (manage.known && !manage.may) return <SettingsScreen title={title}><Say>{NEEDS_EDITOR}</Say></SettingsScreen>;
    if (!spec) return <SettingsScreen title={title}><Say>This deployment does not offer that kind of connection.</Say></SettingsScreen>;

    const entry = detail.data.entry;

    if (added) {
        const unreadable = added.bad && !added.signIn;
        const finish = () => router.replace(connectorsRoutes.install(connectorId, added.made.id) as never);
        return (
            <SettingsScreen title={added.made.name || title} note="Added">
                {result ? (
                    <SignedIn result={result} title={added.made.name || title} connectorId={connectorId} checking={check.isPending}
                        onCheck={() => { if (result.kind === "nothing") check.mutate(result.pending, { onSuccess: setResult }); }} />
                ) : (
                    <>
                        <Note tone={added.bad ? "bad" : "done"} text={added.note} />
                        {unreadable ? <Say>It is saved either way. Fix the address or the token, then read its operations again from its page.</Say> : null}
                        {added.signIn ? <Say>This server signs in through a browser. Nobody can use it until someone does.</Say> : null}
                        {signIn.isError ? <Say tone="bad">{connectorProblem(signIn.error, "Signing in could not be started just now. Sign in from its page.")}</Say> : null}
                        <Actions>
                            {added.signIn ? (
                                <Button
                                    title={signIn.isPending ? "Opening…" : "Sign in"}
                                    busy={signIn.isPending}
                                    onPress={() => signIn.mutate({ connectorId, installId: added.made.id }, { onSuccess: setResult })}
                                />
                            ) : null}
                            <Button title="Done" kind={added.signIn ? "outline" : "primary"} disabled={signIn.isPending} onPress={finish} />
                        </Actions>
                    </>
                )}
            </SettingsScreen>
        );
    }

    const submit = () => {
        setFailure(null);
        const found = { ...problems(list, ready), ...problems(secrets, readyCreds) };
        setShown(found);
        if (Object.keys(found).length > 0) return;
        const wrong = nameProblem(name, "", here.takenNames);
        if (wrong) { setFailure(wrong); return; }
        add.mutate({ entry, kind, name: name.trim(), config: payload(list, ready), credentials: payload(secrets, readyCreds) }, {
            onSuccess: setAdded,
            onError: (problem) => setFailure(isForbidden(problem) ? NEEDS_EDITOR : connectorProblem(problem, "That could not be added.")),
        });
    };

    return (
        <SettingsScreen title={title} note={look?.blurb ?? entry.description ?? null}>
            {look?.signIn ? <Say>{look.signIn}</Say> : null}
            <Field
                label="Name"
                value={name}
                onChange={setName}
                autoCapitalize="none"
                placeholder="Docs search, Postgres (staging)…"
                hint="So people here can recognise this connection."
                editable={!add.isPending}
            />
            <Fields list={list} values={ready} problems={shown} disabled={add.isPending} onChange={(field, value) => setValues({ ...ready, [field]: value })} />
            {secrets.length > 0 ? (
                <Fields list={secrets} values={readyCreds} problems={shown} disabled={add.isPending} onChange={(field, value) => setCreds({ ...readyCreds, [field]: value })} />
            ) : null}
            {failure ? <Say tone="bad">{failure}</Say> : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow disabled={add.isPending} onPress={() => router.back()} />
                <Button title={add.isPending ? "Adding…" : "Add connection"} grow busy={add.isPending} onPress={submit} />
            </View>
        </SettingsScreen>
    );
}
