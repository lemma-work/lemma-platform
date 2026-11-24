import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import type { AccountConnect } from "../connect/accounts";
import { Say } from "../ui";
import type { ConfigSource } from "@web/connect/install";
import { useReachSource } from "./hooks";
import { ACCOUNT_WAIT_MS, GIVE_UP_AFTER_FAILURES, POLL_EVERY_MS, connectProblem } from "./places";

/** Signing in to a provider, then putting what it made to use (the web's
 *  `Account` in `shell/reach.tsx`). Three legs: ask the server for the
 *  provider's page, send the person there in the phone's browser, and find
 *  the account that appears. The provider sends its answer to the server,
 *  not here, so a new usable account on the connector is the signal; when
 *  the browser came back naming one, that one. After the person is back it
 *  keeps looking a while, since a provider's answer can land a little late. */

type Stage = "idle" | "starting" | "away" | "waiting" | "binding";

export function useSignIn({ orgId, connectorId, configSource, authConfigId, bind, onDone }: {
    orgId: string | null;
    connectorId: string;
    configSource?: ConfigSource;
    /** A particular install to authorise, such as an app just made. */
    authConfigId?: string;
    bind: (accountId: string) => Promise<unknown>;
    onDone: () => void;
}) {
    const source = useReachSource();
    const [stage, setStage] = useState<Stage>("idle");
    const [link, setLink] = useState<AccountConnect | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState<string | null>(null);
    const named = useRef<string | null>(null);
    const tell = useRef({ bind, onDone });
    tell.current = { bind, onDone };

    const attach = useCallback(async (accountId: string) => {
        setStage("binding");
        setPending(accountId);
        setError(null);
        try {
            await tell.current.bind(accountId);
            setPending(null);
            tell.current.onDone();
        } catch (problem) {
            setStage("idle");
            setError(connectProblem(problem, "That account could not be used."));
        }
    }, []);

    /** Look for the account once: answers whether it was found. One look at
     *  a time, so a slow answer and the next tick never bind it twice. */
    const looking = useRef(false);
    const look = useCallback(async (current: AccountConnect): Promise<boolean> => {
        if (!orgId || looking.current) return false;
        looking.current = true;
        try {
            const id = named.current ?? await source.findAccount(orgId, connectorId, current.before, current.authConfigId);
            if (!id) return false;
            await attach(id);
            return true;
        } finally {
            looking.current = false;
        }
    }, [attach, connectorId, orgId, source]);

    const open = useCallback(async (current: AccountConnect) => {
        setStage("away");
        setError(null);
        const outcome = await source.signIn(current.authorizeUrl).catch(() => null);
        if (outcome?.connect === "error") {
            setStage("idle");
            setLink(null);
            setError(outcome.reason || "The account was not connected.");
            return;
        }
        if (outcome?.connect === "connected" && outcome.account) named.current = outcome.account;
        setStage("waiting");
        await look(current).catch(() => false);
    }, [look, source]);

    const begin = useCallback(async () => {
        if (!orgId) return;
        setStage("starting");
        setError(null);
        named.current = null;
        try {
            const started = await source.startAccount(orgId, connectorId, { configSource, authConfigId });
            if (!started.authorizeUrl) {
                setStage("idle");
                setError("This one cannot be started from here. It has to be connected under Connectors first.");
                return;
            }
            setLink(started);
            await open(started);
        } catch (problem) {
            setStage("idle");
            setError(connectProblem(problem, "That could not be started."));
        }
    }, [authConfigId, configSource, connectorId, open, orgId, source]);

    /* Back from the provider and nothing yet: ask again every few seconds,
       stop after a run of failures or a quarter of an hour, and hand the
       retry back to the person. */
    useEffect(() => {
        if (stage !== "waiting" || !link) return;
        let stop = false;
        let failures = 0;
        const deadline = Date.now() + ACCOUNT_WAIT_MS;
        const giveUp = (reason: string) => {
            stop = true;
            clearInterval(tick);
            setStage("idle");
            setLink(null);
            setError(reason);
        };
        const tick = setInterval(() => {
            if (Date.now() > deadline) return giveUp("That took too long. Connect again to start over.");
            look(link).then((found) => {
                if (stop) return;
                failures = 0;
                if (found) { stop = true; clearInterval(tick); }
            }).catch((problem: unknown) => {
                if (stop) return;
                failures += 1;
                const reason = connectProblem(problem, "That account could not be used.");
                if (failures >= GIVE_UP_AFTER_FAILURES) giveUp(reason);
                else setError(reason);
            });
        }, POLL_EVERY_MS);
        return () => { stop = true; clearInterval(tick); };
    }, [stage, link, look]);

    return {
        stage,
        error,
        /** An account found, whose binding was refused: try that again. */
        pending,
        begin,
        again: () => { if (link) void open(link); },
        check: () => { if (link) void look(link).catch((problem: unknown) => setError(connectProblem(problem, "That account could not be used."))); },
        retry: () => { if (pending) void attach(pending); },
    };
}

export function Waiting({ children }: { children: string }) {
    return (
        <View accessibilityLiveRegion="polite" style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 }}>
            <ActivityIndicator size="small" color={color.ink3} />
            <Text size={14} tone={color.ink2} style={{ flex: 1, lineHeight: 19 }}>{children}</Text>
        </View>
    );
}

/** The sign-in, drawn: one button, then the wait, then the setting up. */
export function SignInStep({ name, label = "Connect", flow }: { name: string; label?: string; flow: ReturnType<typeof useSignIn> }) {
    if (flow.pending && flow.stage !== "binding" && flow.error) {
        return (
            <View style={{ gap: 8 }}>
                <Say tone="bad">{flow.error}</Say>
                <View style={{ flexDirection: "row" }}>
                    <Button title="Try again" onPress={flow.retry} />
                </View>
            </View>
        );
    }
    if (flow.stage === "binding") return <Waiting>Setting it up…</Waiting>;
    if (flow.stage === "away") return <Waiting>{`Finish in ${name}, then come back here.`}</Waiting>;
    if (flow.stage === "waiting") {
        return (
            <View style={{ gap: 10 }}>
                <Waiting>{`Waiting for ${name}…`}</Waiting>
                <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{`If you closed ${name} before it finished, open it again. This screen notices when it is done.`}</Text>
                {flow.error ? <Say tone="bad">{flow.error}</Say> : null}
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title={`Open ${name} again`} kind="outline" grow onPress={flow.again} />
                    <Button title="Check again" kind="outline" grow onPress={flow.check} />
                </View>
            </View>
        );
    }
    return (
        <View style={{ gap: 8 }}>
            <View style={{ flexDirection: "row" }}>
                <Button title={label} busy={flow.stage === "starting"} onPress={() => void flow.begin()} />
            </View>
            {flow.error ? <Say tone="bad">{flow.error}</Say> : null}
        </View>
    );
}
