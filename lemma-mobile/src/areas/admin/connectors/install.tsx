import { useMemo, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Button } from "../../../ui/press";
import { Confirm, Field, Group, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { Fields } from "../connect/fields";
import { connectorProblem, discoveryNote, installSchema, installTarget, kindNamed } from "@web/connect/install";
import { blank, fields, problems, type Values } from "@web/connect/schema";
import { useConnectorHere, useDeleteInstall, useDetail, useInstallRead, useMakeDefault, useMayManage, useRefreshOperations, useSaveInstall, useSignIn } from "./hooks";
import { accountHint, accountTitle } from "./model";
import { Actions, Note, Rows } from "./parts";
import { editLead, installActs, nameProblem, reauthWords } from "./plan";
import { connectorsRoutes } from "./routes";
import { signInWords } from "./sign-in";

/** One install of a connector, an auth config in the API's words (the
 *  web's install row, as a screen). A connector is a catalog row; an
 *  install is this organization's copy of it, with its own OAuth app or its
 *  own address, and accounts hang off an install, never off the connector.
 *
 *  Anybody may connect an account on it. Making it the default, re-reading
 *  its operations, changing it and removing it are an owner's or an
 *  editor's, and are offered to nobody else. */
export function InstallScreen({ connectorId, installId }: { connectorId: string; installId: string }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const { connector, orgId } = here;
    const manage = useMayManage(orgId);
    const install = here.installs.find((one) => one.id === installId) ?? null;
    const promote = useMakeDefault(orgId);
    const reread = useRefreshOperations(orgId);
    const drop = useDeleteInstall(orgId);
    const signIn = useSignIn(orgId);
    const [said, setSaid] = useState<{ text: string; bad: boolean } | null>(null);
    const [removing, setRemoving] = useState(false);
    const [gone, setGone] = useState(false);

    const back = connector?.title ?? "Connectors";
    if (here.pending) return <SettingsScreen title="Install" back={back}><Reading /></SettingsScreen>;
    if (here.error) return <SettingsScreen title="Install" back={back}><ReadProblem said={problemOf(here.error, "This install could not be read. Try again.")} onRetry={here.refetch} /></SettingsScreen>;
    if (!install || !connector) return <SettingsScreen title="Install" back={back}>{gone ? <Reading /> : <Say>This install is no longer here.</Say>}</SettingsScreen>;

    const acts = installActs(install, connector, here.accounts);
    const on = here.accounts.filter((account) => account.authConfigId === install.id);
    const name = install.name || install.id;
    const target = acts.addressed ? installTarget(install) : null;
    const busy = promote.isPending || reread.isPending || signIn.isPending;

    return (
        <SettingsScreen title={name} note={connector.title} back={back}>
            <Group>
                <Rows>
                    {acts.addressed
                        ? target ? <Row key="where" label="Points at" value={target} /> : null
                        : <Row key="how" label="Signs in through" value={install.config_source === "ORG_CUSTOM" ? "Your own app" : "Lemma’s app"} />}
                    <Row key="default" label="Default" value={install.is_default ? "Yes" : "No"} hint={install.is_default ? `Used wherever ${connector.title} is named without an install` : null} />
                    {acts.off ? <Row key="off" label="Status" value="Off" hint="Nobody can connect on it, and its accounts can only be removed" /> : null}
                    {acts.needsSignIn && !acts.off ? <Row key="signed" label="Signed in" value="Nobody yet" needs /> : null}
                </Rows>
            </Group>

            {said ? <Note tone={said.bad ? "bad" : "quiet"} text={said.text} /> : null}

            {acts.connect ? (
                <Actions>
                    {acts.connect === "sign-in" ? (
                        <Button
                            title={signIn.isPending ? `Opening ${connector.title}…` : "Sign in"}
                            busy={signIn.isPending}
                            disabled={busy}
                            onPress={() => {
                                setSaid(null);
                                signIn.mutate({ connectorId: connector.id, installId: install.id }, {
                                    onSuccess: (result) => setSaid(signInWords(result, name)),
                                    onError: (problem) => setSaid({ text: connectorProblem(problem, "Signing in could not be started."), bad: true }),
                                });
                            }}
                        />
                    ) : (
                        <Button title="Connect an account" onPress={() => router.push(connectorsRoutes.connect(connector.id, { install: install.id }) as never)} />
                    )}
                </Actions>
            ) : null}

            {on.length > 0 ? (
                <Group label="Your accounts on it">
                    {on.map((account, index) => (
                        <Row
                            key={account.id}
                            role="link"
                            label={accountTitle(account, connector)}
                            hint={accountHint(account, null, false) || null}
                            needs={!account.usable}
                            last={index === on.length - 1}
                            onPress={() => router.push(connectorsRoutes.account(connector.id, account.id) as never)}
                        />
                    ))}
                </Group>
            ) : null}

            {manage.may ? (
                <Group label="Manage">
                    {!install.is_default && !acts.off ? (
                        <Row
                            role="button"
                            label={promote.isPending ? "Making it the default…" : "Make it the default"}
                            disabled={busy}
                            onPress={() => {
                                setSaid(null);
                                promote.mutate(install, { onError: (problem) => setSaid({ text: connectorProblem(problem, "It could not be made the default just now."), bad: true }) });
                            }}
                        />
                    ) : null}
                    {acts.addressed ? (
                        <Row
                            role="button"
                            label={reread.isPending ? "Reading its operations…" : "Read its operations again"}
                            hint="After the server or the spec changed"
                            disabled={busy}
                            onPress={() => {
                                setSaid(null);
                                reread.mutate(install, {
                                    onSuccess: (found) => setSaid({ text: discoveryNote(found.status, found.count, null), bad: found.status === "failed" }),
                                    onError: () => setSaid({ text: "Its operations could not be read just now.", bad: true }),
                                });
                            }}
                        />
                    ) : null}
                    {acts.editable ? <Row role="link" label="Edit" hint="Its name and settings" onPress={() => router.push(connectorsRoutes.edit(connector.id, install.id) as never)} /> : null}
                    <Row role="button" label="Remove" danger last onPress={() => setRemoving(true)} />
                </Group>
            ) : null}

            <Confirm
                open={removing}
                title={`Remove ${name}?`}
                body={`Every account on it goes with it, yours and anybody else’s here, and everything that uses them stops. There is no undo.`}
                confirm={drop.isPending ? "Removing…" : "Remove, with its accounts"}
                busy={drop.isPending}
                error={drop.isError ? (isForbidden(drop.error) ? "You may not remove this install." : connectorProblem(drop.error, "It could not be removed. Try again.")) : null}
                onClose={() => { if (!drop.isPending) setRemoving(false); }}
                onConfirm={() => drop.mutate(install, { onSuccess: () => { setGone(true); setRemoving(false); router.back(); } })}
            />
        </SettingsScreen>
    );
}

/** Changing an install somebody set up (the web's `EditInstall`): its name,
 *  the server or database it points at, or the OAuth app it signs in
 *  through, without removing it. The server keeps its accounts and asks
 *  people to sign in again only where the change made their credential
 *  meaningless.
 *
 *  The form is drawn from a fresh read, which comes back masked, so a
 *  secret nobody touched is sent back masked and the server keeps it. The
 *  credential itself is the account's, and is replaced from its own screen. */
export function EditInstallScreen({ connectorId, installId }: { connectorId: string; installId: string }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const detail = useDetail(connectorId);
    const install = here.installs.find((one) => one.id === installId) ?? null;
    const current = useInstallRead(here.orgId, install);
    const kind = useMemo(() => (detail.data && install ? kindNamed(detail.data.entry, install.kind) : null), [detail.data, install]);
    const list = useMemo(() => fields(installSchema(kind)), [kind]);
    const save = useSaveInstall(here.orgId);
    const [name, setName] = useState<string | null>(null);
    /* Null until somebody types, so the form follows the fresh read. */
    const [values, setValues] = useState<Values | null>(null);
    const [shown, setShown] = useState<Record<string, string>>({});
    const [failure, setFailure] = useState<string | null>(null);
    const [done, setDone] = useState<{ note: string; bad: boolean; reauth: number } | null>(null);

    const title = install ? `Edit ${install.name || here.connector?.title || "install"}` : "Edit";
    const back = install?.name || "Install";
    if (here.pending || detail.isLoading || current.isLoading) return <SettingsScreen title={title} back={back}><Reading /></SettingsScreen>;
    if (!install) return <SettingsScreen title={title} back={back}><Say>This install is no longer here.</Say></SettingsScreen>;
    if (detail.isError) return <SettingsScreen title={title} back={back}><ReadProblem said={problemOf(detail.error, "What this connector needs could not be read. Try again.")} onRetry={() => void detail.refetch()} /></SettingsScreen>;

    const typed = name ?? install.name ?? "";
    const ready = values ?? blank(list, current.data?.config ?? install.config ?? null);

    if (done) {
        return (
            <SettingsScreen title={typed.trim() || install.name || "Saved"} note="Saved" back={back}>
                <Note tone={done.bad ? "bad" : "done"} text={done.note} />
                {done.reauth > 0 ? <Note text={reauthWords(done.reauth)} /> : null}
                <View style={{ flexDirection: "row" }}>
                    <Button title="Done" onPress={() => router.back()} />
                </View>
            </SettingsScreen>
        );
    }

    const submit = () => {
        setFailure(null);
        const found = problems(list, ready);
        setShown(found);
        if (Object.keys(found).length > 0) return;
        const wrong = nameProblem(typed, install.name || "", here.takenNames);
        if (wrong) { setFailure(wrong); return; }
        save.mutate({ install, name: typed, list, values: ready }, {
            onSuccess: (answer) => { if (answer.said) setDone({ note: answer.note, bad: answer.bad, reauth: answer.reauth }); else router.back(); },
            onError: (problem) => setFailure(isForbidden(problem) ? "You may not change this install." : connectorProblem(problem, "Those changes could not be saved.")),
        });
    };

    return (
        <SettingsScreen title={title} note={here.connector?.title ?? null} back={back}>
            {current.isError ? <Say tone="bad">Its current settings could not be read, so what is shown may be out of date.</Say> : null}
            <Say>{editLead(kind)}</Say>
            <Field label="Name" value={typed} onChange={setName} autoCapitalize="none" hint="How people here, and their Lemmas, refer to this connection." editable={!save.isPending} />
            <Fields list={list} values={ready} problems={shown} disabled={save.isPending} onChange={(field, value) => setValues({ ...ready, [field]: value })} />
            {failure ? <Say tone="bad">{failure}</Say> : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow disabled={save.isPending} onPress={() => router.back()} />
                <Button title={save.isPending ? "Saving…" : "Save changes"} grow busy={save.isPending} onPress={submit} />
            </View>
        </SettingsScreen>
    );
}
