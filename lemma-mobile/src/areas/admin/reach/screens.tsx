import { View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma, useOrg } from "../../../data/hooks";
import { Button } from "../../../ui/press";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { Group, ReadProblem, Reading, Row, Say, SettingsScreen, isForbidden, problemOf } from "../ui";
import { isCredentialConflict } from "@web/data/accounts";
import { useCatalog, useConnect, useMine, usePodConnectors, usePlaces } from "./hooks";
import { PlaceMark } from "./marks";
import { addressOf, connectPlan, connectProblem, heldElsewhere, listHint, offerOf, ownPlaces, placeKey, placeName, placeNeedsYou, placeStatus, quickWay, reachCount } from "./places";
import { reachRoutes } from "./routes";

const PART = lemmaPart("channels");

/** Where people reach a Lemma (the web's `shell/reach.tsx` `ReachPanel`):
 *  the places it answers now, each opening its own screen, then the places
 *  it could, cheapest first, each saying what it would take before anything
 *  is pressed. One tap where Lemma's own identity, or an account the
 *  organization already has, can answer; a screen of steps everywhere else.
 *
 *  What only the desktop app does (setting a place up on "This Mac") is not
 *  here. */
export function ChannelsScreen({ podId }: PartProps) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "This Lemma";
    const { org } = useOrg();
    const places = usePlaces(podId);
    const catalog = useCatalog(podId);
    const connectors = usePodConnectors(podId);
    const mine = useMine();
    const connect = useConnect(podId, org?.id);

    const on = ownPlaces(places.data ?? []);
    const offer = offerOf(catalog.data ?? [], on, places.isSuccess);
    const elsewhere = heldElsewhere(mine.data ?? [], podId, places.data ?? []);
    const busy = (platform: string) => connect.isPending && connect.variables?.platform === platform;

    const go = (platform: string, podConnector: string | null) => {
        connect.mutate({ platform, podConnector }, {
            onSuccess: (made) => router.push(reachRoutes.place(podId, made.name) as never),
            onError: (problem) => {
                /* Not a failure to stop at: the account is another Lemma's,
                   and the answer is an identity of this one's own. */
                if (podConnector && isCredentialConflict(problem)) router.push(reachRoutes.add(podId, platform, { taken: true }) as never);
            },
        });
    };

    return (
        <SettingsScreen title={PART?.title(name) ?? "Where people reach it"} note={PART?.note(name)} back={lemma?.name ?? "Settings"}>
            {places.isPending ? <Reading label="Reading where it answers" /> : places.isError ? (
                <ReadProblem
                    said={isForbidden(places.error) ? `You may not see where ${name} is reached.` : problemOf(places.error, `Where ${name} answers could not be read.`)}
                    onRetry={() => void places.refetch()}
                />
            ) : on.length ? (
                <Group label="Where it answers">
                    {on.map((surface, index) => {
                        const address = addressOf(surface);
                        const status = placeStatus(surface.status, surface.active);
                        return (
                            <Row
                                key={surface.id}
                                role="link"
                                glyph={<PlaceMark platform={surface.platform} />}
                                label={placeName(surface.platform)}
                                hint={address ? `${address} · ${status}` : status}
                                needs={placeNeedsYou(surface)}
                                last={index === on.length - 1}
                                onPress={() => router.push(reachRoutes.place(podId, surface.name) as never)}
                            />
                        );
                    })}
                </Group>
            ) : (
                <Say>{reachCount(name, 0)}</Say>
            )}

            {catalog.isPending && places.isSuccess ? <Reading label="Reading where it could answer" /> : null}
            {catalog.isError ? (
                <ReadProblem said={problemOf(catalog.error, `Where ${name} could answer could not be read.`)} onRetry={() => void catalog.refetch()} />
            ) : null}

            {offer.length ? (
                <Group label={on.length ? "Somewhere else" : "Pick a place"}>
                    {offer.map((entry, index) => {
                        const plan = connectPlan(entry, connectors.data ?? [], elsewhere.has(placeKey(entry.platform)));
                        const quick = quickWay(plan);
                        return (
                            <Row
                                key={entry.platform}
                                role="link"
                                glyph={<PlaceMark platform={entry.platform} />}
                                label={plan.name}
                                hint={listHint(plan)}
                                disabled={plan.action === "blocked"}
                                last={index === offer.length - 1}
                                onPress={plan.action === "blocked" ? undefined : () => router.push(reachRoutes.add(podId, entry.platform) as never)}
                                end={quick ? (
                                    <View style={{ flexDirection: "row" }}>
                                        <Button title={busy(entry.platform) ? "Connecting…" : "Connect"} height={36} size={14} busy={busy(entry.platform)} disabled={connect.isPending} onPress={() => go(entry.platform, plan.action === "use-account" ? plan.podConnector : null)} />
                                    </View>
                                ) : undefined}
                            />
                        );
                    })}
                </Group>
            ) : null}

            {connect.isError && !(isCredentialConflict(connect.error) && connect.variables?.podConnector) ? (
                <Say tone="bad">{connectProblem(connect.error, `${placeName(connect.variables?.platform ?? "")} could not be connected. Try again.`)}</Say>
            ) : null}
        </SettingsScreen>
    );
}
