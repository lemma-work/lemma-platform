import { useMemo, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { Button } from "../../../ui/press";
import { Group, ChoiceSheet, Confirm, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { Fields } from "../connect/fields";
import { connectorProblem } from "@web/connect/install";
import { blank, payload, problems, type Values } from "@web/connect/schema";
import { useBindInstallation, useConnectorHere, useDetail, useDisconnect, useFinishInstall, useRotate, useSignIn } from "./hooks";
import { accountTitle, dayWords, installsShown, statusWords } from "./model";
import { Actions, Note, Rows } from "./parts";
import { accountActs, accountTrouble, rotateFields } from "./plan";
import { connectorsRoutes } from "./routes";
import { installWords, repositoriesWords, signInWords } from "./sign-in";
import type { InstallationChoice } from "./source";

/** One of your accounts on a connector (the web's account row, as a
 *  screen): what state it is in, and the one thing that fixes it, which is
 *  a different thing for each state. An account authorised and not
 *  installed is installed (or its installation chosen); one signed out
 *  signs in again on the same install, which keeps it and everything pinned
 *  to it; one that holds a credential has it replaced in place. Removing it
 *  comes last, and says what goes with it. */
export function AccountScreen({ connectorId, accountId }: { connectorId: string; accountId: string }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const { connector, orgId } = here;
    const account = here.accounts.find((one) => one.id === accountId) ?? null;
    const install = account ? here.installs.find((one) => one.id === account.authConfigId) ?? null : null;
    const finish = useFinishInstall(orgId);
    const bind = useBindInstallation(orgId);
    const signIn = useSignIn(orgId);
    const drop = useDisconnect(orgId);
    const [choices, setChoices] = useState<InstallationChoice[] | null>(null);
    const [said, setSaid] = useState<{ text: string; bad: boolean } | null>(null);
    const [removing, setRemoving] = useState(false);
    /* Kept once removed, so the screen does not flash "no longer here"
       on its way back. */
    const [gone, setGone] = useState(false);

    const back = connector?.title ?? "Connectors";
    if (here.pending) return <SettingsScreen title="Account" back={back}><Reading /></SettingsScreen>;
    if (here.error) return <SettingsScreen title="Account" back={back}><ReadProblem said={problemOf(here.error, "This account could not be read. Try again.")} onRetry={here.refetch} /></SettingsScreen>;
    if (!account || !connector) {
        return <SettingsScreen title="Account" back={back}>{gone ? <Reading /> : <Say>This account is no longer here.</Say>}</SettingsScreen>;
    }

    const acts = accountActs(account, install, connector);
    const title = accountTitle(account, connector);
    const named = installsShown(connector, here.installs);
    const busy = finish.isPending || bind.isPending || signIn.isPending;
    const connected = dayWords(account.createdAt);

    const startFinish = () => {
        setSaid(null);
        finish.mutate(account.id, {
            onSuccess: (step) => {
                if (step.kind === "choices" && step.choices.length > 0) setChoices(step.choices);
                setSaid(installWords(step, connector.title));
            },
            onError: (problem) => setSaid({ text: connectorProblem(problem, "The installation could not be started."), bad: true }),
        });
    };

    return (
        <SettingsScreen title={title} note={connector.title} back={back}>
            <Group>
                <Rows>
                    <Row key="status" label="Status" value={statusWords(account)} hint={account.usable ? null : accountTrouble(account)} needs={!account.usable} />
                    {account.ref ? <Row key="ref" label="Account" value={account.ref} /> : null}
                    {account.isDefault ? <Row key="default" label="Default" value={`For ${connector.title}`} /> : null}
                    {install && named ? (
                        <Row key="install" role="link" label="Install" value={install.name || install.id} onPress={() => router.push(connectorsRoutes.install(connector.id, install.id) as never)} />
                    ) : null}
                    {connected ? <Row key="since" label="Connected" value={connected} /> : null}
                </Rows>
            </Group>

            {said ? <Note tone={said.bad ? "bad" : "quiet"} text={said.text} /> : null}

            {acts.unfinished || acts.reconnect ? (
                <Actions>
                    {acts.unfinished ? (
                        <Button
                            title={finish.isPending ? "Checking…" : acts.choose ? "Choose the installation" : `Install on ${connector.title}`}
                            busy={finish.isPending}
                            disabled={busy}
                            onPress={startFinish}
                        />
                    ) : null}
                    {acts.reconnect ? (
                        <Button
                            title={signIn.isPending ? `Opening ${connector.title}…` : "Sign in again"}
                            busy={signIn.isPending}
                            disabled={busy}
                            onPress={() => {
                                setSaid(null);
                                signIn.mutate({ connectorId: connector.id, installId: install?.id ?? null }, {
                                    onSuccess: (result) => setSaid(signInWords(result, connector.title)),
                                    onError: (problem) => setSaid({ text: connectorProblem(problem, "Signing in could not be started."), bad: true }),
                                });
                            }}
                        />
                    ) : null}
                </Actions>
            ) : null}

            <Group label="Manage">
                {acts.rotate ? (
                    <Row role="link" label="Replace the credential" hint="Keeps the account, and everything that uses it" onPress={() => router.push(connectorsRoutes.rotate(connector.id, account.id) as never)} />
                ) : null}
                <Row role="button" label="Remove" danger last onPress={() => setRemoving(true)} />
            </Group>

            <ChoiceSheet
                open={choices !== null}
                title="Which installation it speaks for"
                value={null}
                choices={(choices ?? []).map((choice) => ({
                    value: choice.installationId,
                    label: choice.login ?? choice.installationId,
                    hint: [choice.type, repositoriesWords(choice.repositories)].filter(Boolean).join(" · ") || null,
                }))}
                onClose={() => setChoices(null)}
                onPick={(installationId) => {
                    setSaid(null);
                    bind.mutate({ accountId: account.id, installationId }, {
                        onSuccess: () => setSaid({ text: "Done. It speaks for that installation now.", bad: false }),
                        onError: (problem) => setSaid({ text: connectorProblem(problem, "That installation could not be used."), bad: true }),
                    });
                }}
            />
            <Confirm
                open={removing}
                title={`Remove ${title}?`}
                body={`Everything that uses this account stops, in every Lemma here.${acts.rotate ? " Replacing its credential instead keeps all of it." : ""} There is no undo.`}
                confirm={drop.isPending ? "Removing…" : "Remove"}
                busy={drop.isPending}
                error={drop.isError ? (isForbidden(drop.error) ? "You may not remove this account." : problemOf(drop.error, "It could not be removed. Try again.")) : null}
                onClose={() => { if (!drop.isPending) setRemoving(false); }}
                onConfirm={() => drop.mutate(account.id, { onSuccess: () => { setGone(true); setRemoving(false); router.back(); } })}
            />
        </SettingsScreen>
    );
}

/** Replacing a credential on an account that already exists (the web's
 *  `RotateDialog`). Its own screen rather than a mode of connecting, because
 *  it is a different act: this keeps the account's id, and removing and
 *  connecting again does not, so everything pinned to the old id survives
 *  this and does not survive that. */
export function RotateScreen({ connectorId, accountId }: { connectorId: string; accountId: string }) {
    const router = useRouter();
    const here = useConnectorHere(connectorId);
    const detail = useDetail(connectorId);
    const account = here.accounts.find((one) => one.id === accountId) ?? null;
    const install = account ? here.installs.find((one) => one.id === account.authConfigId) ?? null : null;
    const list = useMemo(() => (detail.data ? rotateFields(detail.data.entry, install) : []), [detail.data, install]);
    const [values, setValues] = useState<Values>({});
    const [shown, setShown] = useState<Record<string, string>>({});
    const rotate = useRotate(here.orgId);
    const ready = list.length > 0 && Object.keys(values).length === 0 ? blank(list) : values;
    const name = account ? accountTitle(account, here.connector) : "Account";

    const submit = () => {
        const found = problems(list, ready);
        setShown(found);
        if (Object.keys(found).length > 0 || !account) return;
        rotate.mutate({ accountId: account.id, credentials: payload(list, ready) }, { onSuccess: () => router.back() });
    };

    return (
        <SettingsScreen title="Replace the credential" note={name} back={name}>
            {here.pending || detail.isLoading ? (
                <Reading />
            ) : !account ? (
                <Say>This account is no longer here.</Say>
            ) : detail.isError ? (
                <ReadProblem said={problemOf(detail.error, "What this connector needs could not be read. Try again.")} onRetry={() => void detail.refetch()} />
            ) : list.length === 0 ? (
                <Say tone="bad">This connector does not describe a credential that can be replaced from here.</Say>
            ) : (
                <>
                    <Say>Everything that uses this account keeps working, with the new credential. Check that it has the access they need.</Say>
                    <Fields list={list} values={ready} problems={shown} disabled={rotate.isPending} onChange={(field, value) => setValues({ ...ready, [field]: value })} />
                    {rotate.isError ? <Say tone="bad">{isForbidden(rotate.error) ? "You may not change this account." : problemOf(rotate.error, "That credential was not accepted.")}</Say> : null}
                    <View style={{ flexDirection: "row", gap: 10 }}>
                        <Button title="Cancel" kind="outline" grow disabled={rotate.isPending} onPress={() => router.back()} />
                        <Button title={rotate.isPending ? "Replacing…" : "Replace"} grow busy={rotate.isPending} onPress={submit} />
                    </View>
                </>
            )}
        </SettingsScreen>
    );
}
