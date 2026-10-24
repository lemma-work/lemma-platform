import { useState } from "react";
import { ScrollView } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import { LemmaFace } from "../../../ui/face";
import { Sheet } from "../../../ui/sheet";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { Confirm, Group, ReadProblem, Reading, Row, RowIcon, Say, SettingsScreen, isForbidden } from "../ui";
import { useAskable, useDisconnect, useLinks, useMayConnect } from "./hooks";
import { disconnectSaid, howItAsks, type PodLink } from "./model";
import { worksWithRoutes } from "./routes";
import { sharedLine } from "@web/space/pod-links";

export { ConnectScreen } from "./connect";

/** A second sheet waits for the first to finish leaving: iOS will not show
 *  one while another is still being dismissed. */
function afterSheet(then: () => void) {
    setTimeout(then, 350);
}

/** The other Lemmas this one works with: the ones it can ask, and the ones
 *  allowed to ask it, with the way to let another in (the web's "Teammates"
 *  part of About). A Lemma opens on its own page. */
export function WorksWithScreen({ podId }: PartProps) {
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const part = lemmaPart("works-with")!;
    return (
        <SettingsScreen title={part.title(name)} note={part.note(name)} back={lemma?.name ?? "Settings"}>
            <CanAsk podId={podId} name={name} />
            <AskedBy podId={podId} name={name} />
        </SettingsScreen>
    );
}

/** The other Lemmas this one can ask: as you, or allowed by them. Partly
 *  yours: "as you" means you are in both, so someone else sees their own.
 *  The same list the Lemma reads before it asks. */
function CanAsk({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const askable = useAskable(podId);
    return (
        <Group label={`${name} can ask`}>
            {askable.isPending ? <Reading /> : null}
            {askable.isError ? <ReadProblem said={`Couldn’t load who ${name} can ask.`} onRetry={() => void askable.refetch()} /> : null}
            {askable.isSuccess && askable.data.length === 0 ? (
                <Say>Nobody yet. {name} can ask any other Lemma you’re in, or one that lets it.</Say>
            ) : null}
            {(askable.data ?? []).map((other, index, all) => (
                <Row
                    key={other.id}
                    role="link"
                    glyph={<LemmaFace id={other.id} icon={other.iconUrl} size={28} />}
                    label={other.name}
                    hint={[howItAsks(other), other.description ?? ""].filter(Boolean).join(" · ") || null}
                    last={index === all.length - 1}
                    onPress={() => router.push(routes.lemma(other.id) as never)}
                />
            ))}
        </Group>
    );
}

/** The Lemmas allowed to ask this one with nobody present. Anybody here may
 *  see which; only whoever may change who has access here may let another
 *  in, change what one reads, or stop it. */
function AskedBy({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const may = useMayConnect(podId);
    const links = useLinks(podId);
    const disconnect = useDisconnect(podId);
    const [open, setOpen] = useState<PodLink | null>(null);
    const [ending, setEnding] = useState<PodLink | null>(null);
    const list = links.data ?? [];
    if (links.isSuccess && list.length === 0 && !may.allowed) return null;
    return (
        <Group label={`Can ask ${name}`} foot={links.isSuccess && list.length === 0 ? `No other Lemma can ask ${name} with nobody present yet.` : undefined}>
            {may.allowed ? (
                <Row
                    role="link"
                    glyph={<RowIcon name="plus" />}
                    label={`Let another Lemma ask ${name}`}
                    last={links.isSuccess && list.length === 0}
                    onPress={() => router.push(worksWithRoutes.connect(podId) as never)}
                />
            ) : null}
            {links.isPending ? <Reading /> : null}
            {links.isError ? <ReadProblem said={`Couldn’t load who can ask ${name}.`} onRetry={() => void links.refetch()} /> : null}
            {list.map((link, index) => (
                <Row
                    key={link.podId}
                    glyph={<LemmaFace id={link.podId} icon={link.iconUrl} size={28} />}
                    label={link.name}
                    hint={sharedLine(link.shared) + (link.stewardName ? ` · connected by ${link.stewardName}` : "")}
                    last={index === list.length - 1}
                    onPress={() => (may.allowed ? setOpen(link) : router.push(routes.lemma(link.podId) as never))}
                />
            ))}

            <Sheet open={open !== null} onClose={() => setOpen(null)} title={open?.name}>
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8 }}>
                    {open ? (
                        <>
                            <Row role="link" label={`Open ${open.name}`} onPress={() => { const at = open.podId; setOpen(null); router.push(routes.lemma(at) as never); }} />
                            <Row role="link" label="Change what it reads" hint={sharedLine(open.shared)} onPress={() => { const at = open.podId; setOpen(null); router.push(worksWithRoutes.connect(podId, at) as never); }} />
                            <Row label={`Disconnect ${open.name}`} danger last onPress={() => { const chosen = open; disconnect.reset(); setOpen(null); afterSheet(() => setEnding(chosen)); }} />
                        </>
                    ) : null}
                </ScrollView>
            </Sheet>

            <Confirm
                open={ending !== null}
                title={`Disconnect ${ending?.name ?? ""}?`}
                body={ending ? disconnectSaid(ending, name) : ""}
                confirm={disconnect.isPending ? "Disconnecting…" : "Disconnect"}
                busy={disconnect.isPending}
                error={disconnect.isError ? (isForbidden(disconnect.error) ? `Only an admin of ${name} can disconnect a Lemma.` : "That couldn’t be disconnected. Try again.") : null}
                onClose={() => { if (!disconnect.isPending) setEnding(null); }}
                onConfirm={() => { if (ending) disconnect.mutate(ending, { onSuccess: () => setEnding(null) }); }}
            />
        </Group>
    );
}
