import { useCallback, useEffect, useRef, useState } from "react";
import { Linking, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation } from "@tanstack/react-query";
import { useLemma, useOrg } from "../../../data/hooks";
import { Loading } from "../../../ui/chrome";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { createThenBind } from "../connect/accounts";
import { Fields } from "../connect/fields";
import { lemmaPart } from "../sections";
import { Group, ReadProblem, Say, SettingsScreen, problemOf } from "../ui";
import { isCredentialConflict } from "@web/data/accounts";
import { blank, fields, payload, problems, type Values } from "@web/connect/schema";
import { useCatalog, useConnect, useMine, usePodConnectors, usePlaces, useReachSource, useRefreshReach } from "./hooks";
import { Body, Heading, OwnApp, Quiet, SlackManagerStep } from "./own-app";
import { OWN_APP, POLL_EVERY_MS, GIVE_UP_AFTER_FAILURES, connectPlan, connectProblem, connectWay, guidedState, heldElsewhere, placeKey, placeName, quickWay, systemLine } from "./places";
import { reachRoutes } from "./routes";
import { SignInStep, Waiting, useSignIn } from "./sign-in";
import type { GuidedSetup } from "./types";
import type { Connectable } from "@web/data/connectable";

const PART = lemmaPart("channels");

/** Connecting one place (the web's `Focused`, with its one-tap row folded
 *  in): what it would take, said first; the one-tap way when there is one
 *  (Lemma's own identity, or an account the organization already holds);
 *  then the way with an account, number or bot of its own: a token typed
 *  in, Telegram's setup bot, Slack's own app, or signing in to the provider.
 *  When it is done, the place's own screen. */
export function ConnectScreen({ podId, platform, taken = false }: { podId: string; platform: string; taken?: boolean }) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const space = lemma?.name ?? "This Lemma";
    const { org } = useOrg();
    const catalog = useCatalog(podId);
    const places = usePlaces(podId);
    const connectors = usePodConnectors(podId);
    const mine = useMine();
    const source = useReachSource();
    const refresh = useRefreshReach(podId, org?.id);
    const connect = useConnect(podId, org?.id);
    const [conflict, setConflict] = useState(taken);
    const [unfinished, setUnfinished] = useState<string | null>(null);
    const entry = catalog.data?.find((one) => one.platform === platform) ?? null;
    const back = PART?.label ?? "Back";

    /* Done, by any way: read the places again and open the one just made. */
    const finish = useCallback(async () => {
        refresh();
        try {
            const now = await source.places(podId);
            const made = now.find((one) => placeKey(one.platform) === placeKey(platform) && one.mine);
            if (made) router.replace(reachRoutes.place(podId, made.name) as never);
            else setUnfinished("Connected. Look again in a moment to finish setting it up.");
        } catch {
            setUnfinished("Connected, but it could not be read back. Look again in a moment.");
        }
    }, [platform, podId, refresh, router, source]);

    if (catalog.isPending || places.isPending) return <SettingsScreen title={placeName(platform)} back={back}><Loading /></SettingsScreen>;
    if (catalog.isError || !entry) {
        return (
            <SettingsScreen title={placeName(platform)} back={back}>
                {catalog.isError
                    ? <ReadProblem said={problemOf(catalog.error, "Where it could answer could not be read.")} onRetry={() => void catalog.refetch()} />
                    : <Say>{`${placeName(platform)} is not offered on this server.`}</Say>}
            </SettingsScreen>
        );
    }

    const already = (places.data ?? []).find((one) => one.mine && placeKey(one.platform) === placeKey(platform));
    const plan = connectPlan(entry, connectors.data ?? [], heldElsewhere(mine.data ?? [], podId, places.data ?? []).has(placeKey(platform)));
    const quick = quickWay(plan) && !conflict;

    return (
        <SettingsScreen title={plan.name} note={plan.note} back={back}>
            {already ? (
                <View style={{ gap: 8 }}>
                    <Say>{`${space} already answers on ${placeName(platform)}.`}</Say>
                    <Quiet label="Open it" onPress={() => router.replace(reachRoutes.place(podId, already.name) as never)} />
                </View>
            ) : plan.action === "blocked" ? (
                <Say>Nothing can connect here until whoever runs this server sets it up.</Say>
            ) : (
                <>
                    {quick ? (
                        <Group label="Ready now">
                            <View style={{ gap: 10, paddingTop: 2 }}>
                                <Body>{plan.action === "use-account" ? plan.using ?? "" : systemLine(entry, space)}</Body>
                                <View style={{ flexDirection: "row" }}>
                                    <Button
                                        title={connect.isPending ? "Connecting…" : "Connect"}
                                        busy={connect.isPending}
                                        onPress={() => connect.mutate({ platform, podConnector: plan.action === "use-account" ? plan.podConnector : null }, {
                                            onSuccess: (made) => router.replace(reachRoutes.place(podId, made.name) as never),
                                            onError: (problem) => { if (isCredentialConflict(problem)) setConflict(true); },
                                        })}
                                    />
                                </View>
                                {connect.isError && !isCredentialConflict(connect.error) ? <Say tone="bad">{connectProblem(connect.error, `${plan.name} could not be connected. Try again.`)}</Say> : null}
                            </View>
                        </Group>
                    ) : null}
                    {!quick || plan.ownWay ? (
                        <Group label={quick ? "Or use your own account or bot" : undefined}>
                            <View style={{ gap: 12, paddingTop: quick ? 4 : 0 }}>
                                {(conflict || plan.action === "own") && !OWN_APP.has(entry.platform) ? <Say>{`${plan.name} is already in use elsewhere in this organization, so ${space} needs an identity of its own.`}</Say> : null}
                                <Ways podId={podId} orgId={org?.id ?? null} space={space} entry={entry} taken={conflict} onDone={() => void finish()} />
                            </View>
                        </Group>
                    ) : null}
                    {unfinished ? (
                        <View style={{ gap: 8 }}>
                            <Say>{unfinished}</Say>
                            <Quiet label="Look again" onPress={() => void finish()} />
                        </View>
                    ) : null}
                </>
            )}
        </SettingsScreen>
    );
}

/** The way in with something of its own, chosen as the web chooses it. */
function Ways({ podId, orgId, space, entry, taken, onDone }: { podId: string; orgId: string | null; space: string; entry: Connectable; taken: boolean; onDone: () => void }) {
    const source = useReachSource();
    const name = entry.title || placeName(entry.platform);
    const canOwn = OWN_APP.has(entry.platform);
    const hasCredentials = fields(entry.credentialSchema).length > 0;
    const [custom, setCustom] = useState(false);
    const [ownApp, setOwnApp] = useState(canOwn && (taken || entry.effort === "unavailable"));
    useEffect(() => { if (taken && canOwn) setOwnApp(true); }, [taken, canOwn]);
    const way = connectWay(entry, { custom, ownApp });
    const flow = useSignIn({
        orgId,
        connectorId: entry.connectorId,
        bind: (accountId) => source.connectOwnAccount(podId, entry.platform, accountId),
        onDone,
    });

    if (way === "credentials") {
        return (
            <View style={{ gap: 12 }}>
                <Heading>{entry.platform === "TELEGRAM" ? "Use a bot you already made" : `Connect your ${name} account`}</Heading>
                {entry.platform === "TELEGRAM" ? <Body>Paste the token @BotFather gave you when you made the bot. Send it /token to see it again.</Body> : null}
                <Credentials podId={podId} orgId={orgId} entry={entry} onDone={onDone} />
                {entry.guided ? <Quiet label="Make a new bot instead" onPress={() => setCustom(false)} /> : null}
            </View>
        );
    }
    if (way === "own-app") {
        return (
            <View style={{ gap: 12 }}>
                <Heading>{`Give ${space} a Slack app of its own`}</Heading>
                <Body>{taken
                    ? "That Slack account already answers as another Lemma in this organization. One account answers under one name, and one Slack app is one bot user, so this one needs an app of its own."
                    : `One Slack app is one bot user, so ${space} needs an app under its own name.`}</Body>
                <OwnApp podId={podId} orgId={orgId} space={space} entry={entry} onDone={onDone} />
            </View>
        );
    }
    if (way === "slack-manager") return <SlackManagerStep podId={podId} orgId={orgId} space={space} entry={entry} onReady={() => setOwnApp(true)} />;
    if (way === "guided") {
        return (
            <View style={{ gap: 12 }}>
                <Heading>{`Give ${space} a Telegram bot of its own`}</Heading>
                <Body>{`Lemma's setup bot makes it for you and names it after ${space}. You will not need to touch BotFather.`}</Body>
                <Guided podId={podId} onDone={onDone} />
                {hasCredentials ? <Quiet label="Use a bot you already made" onPress={() => setCustom(true)} /> : null}
            </View>
        );
    }
    return (
        <View style={{ gap: 12 }}>
            <Heading>{`Connect ${name} to ${space}`}</Heading>
            {way === "account" ? (
                <>
                    <Body>{(entry.hostedOAuth ? `${name} will ask whether Lemma may act for you. ` : `${name} signs in through an app registered for this organization. `)
                        + `Nothing is sent anywhere until you say so. This only gives ${space} somewhere to answer.`}</Body>
                    <SignInStep name={name} flow={flow} />
                </>
            ) : <Say>{`${name} isn't set up on this server yet. Whoever runs it can add it.`}</Say>}
            {canOwn ? <Quiet label={`Or give ${space} a Slack app of its own`} onPress={() => setOwnApp(true)} /> : null}
        </View>
    );
}

/** A credential typed here (a bot token), made into an account and put to
 *  use at once. A refused binding removes the account again
 *  (`createThenBind`), and the values stay, so trying again makes one. */
function Credentials({ podId, orgId, entry, onDone }: { podId: string; orgId: string | null; entry: Connectable; onDone: () => void }) {
    const source = useReachSource();
    const list = fields(entry.credentialSchema);
    const [values, setValues] = useState<Values>(() => blank(list));
    const [wrong, setWrong] = useState<Record<string, string>>({});
    const connect = useMutation({
        mutationFn: () => {
            if (!orgId) throw new Error("No organization is open.");
            return createThenBind(
                () => source.createAccount(orgId, entry, payload(list, values)),
                (id) => source.connectOwnAccount(podId, entry.platform, id),
                (id) => source.removeAccount(orgId, id),
            );
        },
        onSuccess: () => { setValues(blank(list)); onDone(); },
    });
    return (
        <View style={{ gap: 14 }}>
            <Fields list={list} values={values} problems={wrong} disabled={connect.isPending} onChange={(field, value) => setValues((current) => ({ ...current, [field]: value }))} />
            {connect.isError ? <Say tone="bad">{connectProblem(connect.error, "That was not accepted.")}</Say> : null}
            <View style={{ flexDirection: "row" }}>
                <Button
                    title={connect.isPending ? "Connecting…" : "Connect"}
                    busy={connect.isPending}
                    onPress={() => {
                        const found = problems(list, values);
                        setWrong(found);
                        if (!Object.keys(found).length) connect.mutate();
                    }}
                />
            </View>
        </View>
    );
}

/** Lemma's setup bot makes a Telegram bot of the Lemma's own: started here,
 *  finished in Telegram, noticed back here. Its waiting is a state of its
 *  own, not a spinner that lies, and it stops asking after a run of
 *  failures. */
function Guided({ podId, onDone }: { podId: string; onDone: () => void }) {
    const source = useReachSource();
    const [setup, setSetup] = useState<GuidedSetup | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [starting, setStarting] = useState(false);
    const state = guidedState(setup);
    const done = useRef(onDone);
    done.current = onDone;

    useEffect(() => {
        if (!setup || state !== "waiting") return;
        let stop = false;
        let failures = 0;
        const tick = setInterval(() => {
            if (guidedState(setup) === "failed") {
                stop = true;
                clearInterval(tick);
                setSetup((current) => current && { ...current });
                return;
            }
            source.checkGuided(podId, setup.setupId).then((next) => {
                if (stop) return;
                failures = 0;
                setError(null);
                setSetup(next);
                if (guidedState(next) === "ready") done.current();
            }).catch(() => {
                if (stop) return;
                failures += 1;
                if (failures < GIVE_UP_AFTER_FAILURES) { setError("Could not check on the setup. Trying again."); return; }
                stop = true;
                clearInterval(tick);
                setError("Still cannot check on this setup. Go back and open it again to look.");
            });
        }, POLL_EVERY_MS);
        return () => { stop = true; clearInterval(tick); };
    }, [setup, state, podId, source]);

    if (state === "none") {
        return (
            <View style={{ gap: 8 }}>
                <View style={{ flexDirection: "row" }}>
                    <Button
                        title={starting ? "Starting…" : "Make a bot of its own"}
                        busy={starting}
                        onPress={() => {
                            setStarting(true);
                            setError(null);
                            source.startGuided(podId, "TELEGRAM")
                                .then((next) => { setSetup(next); if (guidedState(next) === "ready") onDone(); })
                                .catch((problem: unknown) => setError(connectProblem(problem, "That could not be started.")))
                                .finally(() => setStarting(false));
                        }}
                    />
                </View>
                {error ? <Say tone="bad">{error}</Say> : null}
            </View>
        );
    }
    if (state === "failed") {
        return (
            <View style={{ gap: 8 }}>
                <Say tone="bad">{setup?.error || "This setup ran out or failed."}</Say>
                <View style={{ flexDirection: "row" }}>
                    <Button title="Start again" kind="outline" onPress={() => { setSetup(null); setError(null); }} />
                </View>
            </View>
        );
    }
    if (state === "ready") return <Say tone="done">{`${setup?.botUsername ?? "Its bot"} is live.`}</Say>;
    return (
        <View style={{ gap: 10 }}>
            <View style={{ flexDirection: "row" }}>
                <Button title="Open Telegram" onPress={() => void Linking.openURL(setup!.launchUrl).catch(() => setError("Telegram could not be opened on this phone."))} />
            </View>
            <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>{`${setup?.managerBot || "Lemma's setup bot"} asks you for a name, then makes the bot. This screen notices when it is done, so you can come back to it.`}</Text>
            {error ? <Say tone="bad">{error}</Say> : null}
            <Waiting>Waiting for Telegram…</Waiting>
        </View>
    );
}
