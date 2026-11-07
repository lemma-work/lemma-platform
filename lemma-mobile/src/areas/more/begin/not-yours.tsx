import { useEffect, useRef } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useLemmas, useOrg } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import { BackLink, Loading } from "../../../ui/chrome";
import { Button, Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Say, problemOf } from "../../admin/ui";
import { useAskToJoin, useJoinStanding, useLemmaHome } from "./hooks";
import { JOIN_WORDS, joinState } from "./model";

/** The door, from outside it (the web's `NotYours`): a Lemma that is not in
 *  your list. No such Lemma; let in already, and opening; asked, waiting;
 *  or outside it, with "Ask to join". Whether asking admits on the spot or
 *  waits for somebody is the Lemma's own rule and is not readable from out
 *  here, so the button asks and the answer says.
 *
 *  There is no name to show: everything known about a Lemma is read from
 *  it, and it is what is closed. The id is shown small at the foot, for
 *  somebody who was sent two links and needs to say which.
 *
 *  Content only, so a Lemma's page can draw it in place and `(in)/join`
 *  can draw it as a screen. `onArrived` runs once the Lemma is in your
 *  list; by default it opens it. */
export function NotYours({ podId, onArrived }: { podId: string; onArrived?: () => void }) {
    const router = useRouter();
    const key = useCacheKey();
    const cache = useQueryClient();
    const { lemmas, pending: listing } = useLemmas();
    const { org, orgs, pick } = useOrg();
    const home = useLemmaHome(podId);
    const standing = useJoinStanding(podId);
    const ask = useAskToJoin(podId);
    const state = joinState({ standing: standing.data, asked: ask.data, standingError: standing.error, askError: ask.error });
    const words = JOIN_WORDS[state];
    const listed = lemmas.some((one) => one.id === podId);

    /* Let in, and now in the list: walk in, once. */
    const arrived = useRef(false);
    const arrive = useRef(onArrived);
    arrive.current = onArrived;
    useEffect(() => {
        if (state !== "admitted" || !listed || arrived.current) return;
        arrived.current = true;
        if (arrive.current) arrive.current();
        else router.replace(routes.lemma(podId) as never);
    }, [state, listed, podId, router]);

    /* Let in before this screen was opened: the list may not know yet. */
    const looked = useRef(false);
    useEffect(() => {
        if (state !== "admitted" || listed || looked.current) return;
        looked.current = true;
        void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
    }, [state, listed, cache, key]);

    /* One you can open: in another of your organizations, open it there; in
       this one, the list was read before you were let in, so read it again.
       Once listed, the Lemma's own page takes over from this. */
    const elsewhere = home.data && home.data !== org?.id ? orgs.find((one) => one.id === home.data) ?? null : null;
    const here = Boolean(home.data) && home.data === org?.id && !listed;
    const reread = useRef(false);
    useEffect(() => {
        if (elsewhere) pick(elsewhere.id);
    }, [elsewhere, pick]);
    useEffect(() => {
        if (!here || reread.current) return;
        reread.current = true;
        void cache.invalidateQueries({ queryKey: [key, "lemmas"] });
    }, [here, cache, key]);
    if (elsewhere) return <Loading label={"Opening it in " + elsewhere.name} />;
    if (here && (listing || !reread.current)) return <Loading label="Opening it" />;

    if ((standing.isPending || home.isPending) && !ask.data) return <Loading label="Looking at this Lemma" />;

    return (
        <View style={{ gap: 14, paddingTop: 8 }}>
            <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, lineHeight: 29 }}>{words.title}</Text>
            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{words.body}</Text>

            {state === "closed" ? (
                <View style={{ flexDirection: "row" }}>
                    <Button title={ask.isPending ? "Asking…" : "Ask to join"} busy={ask.isPending} height={48} onPress={() => ask.mutate()} />
                </View>
            ) : null}
            {state === "admitted" ? (
                /* A way out of a wait that has stopped moving: the list is
                   what decides whether it can be opened. */
                <Press
                    label="Still here? Look again"
                    disabled={listing}
                    onPress={() => void cache.invalidateQueries({ queryKey: [key, "lemmas"] })}
                    style={{ alignSelf: "flex-start", minHeight: 44, justifyContent: "center" }}
                >
                    <Text size={15} weight="medium">Still here? Look again</Text>
                </Press>
            ) : null}
            {ask.error && state !== "missing" ? <Say tone="bad">{problemOf(ask.error, "Couldn’t send the ask. Try again.")}</Say> : null}

            <Num size={12.5} tone={color.ink3} selectable style={{ paddingTop: 10 }}>{podId}</Num>
        </View>
    );
}

/** `(in)/join/[podId]`: the same, as a screen of its own. */
export function JoinScreen({ podId }: { podId: string }) {
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", paddingTop: 4, paddingLeft: 6 }}>
                <BackLink />
            </View>
            <View style={{ paddingHorizontal: 20 }}>
                <NotYours key={podId} podId={podId} />
            </View>
        </SafeAreaView>
    );
}
