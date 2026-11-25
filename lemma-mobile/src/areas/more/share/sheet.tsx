import { useEffect, useState } from "react";
import { ScrollView, Share, View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma, useMe } from "../../../data/hooks";
import { copyText } from "../../../thread/clipboard";
import { Glyph } from "../../../thread/glyphs";
import { PersonFace } from "../../../ui/face";
import { Button } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { useMayManage, usePodMembers } from "../../admin/people/hooks";
import { accessOf } from "../../admin/people/model";
import { peopleRoutes } from "../../admin/people/routes";
import { Group, isForbidden, problemOf, ReadProblem, Reading, Row, RowIcon, Say } from "../../admin/ui";
import { ShareSheet as FileLinkSheet } from "../../files/share-sheet";
import { useLiveLinks, useReach, useShareAddress } from "./hooks";
import { linkRow, liveLinksLine, onlyReader, shareTitle, whoCanOpen } from "./model";
import type { ShareSubject } from "./source";

export type { ShareSubject } from "./source";

/** Share a Lemma, a file, a table or an app: one sheet whatever it is, as
 *  the web has one (`space/share-sheet.tsx`). Who may add people adds them
 *  here; everyone sees who has access, and for a thing in the Lemma, who can
 *  open it; then the link, to send or copy. A file can also go past the
 *  Lemma as a public link, which opens the file's own link sheet over this
 *  one (`files/share-sheet.tsx`).
 *
 *  Nothing is read until it opens: the sheet's body mounts with it. */
export function ShareSheet({ podId, subject, open, onClose }: {
    podId: string;
    subject: ShareSubject;
    open: boolean;
    onClose: () => void;
}) {
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "this Lemma";
    return (
        <Sheet open={open} onClose={onClose} title={shareTitle(subject, lemmaName)} tall>
            <ShareBody podId={podId} subject={subject} lemmaName={lemmaName} onClose={onClose} />
        </Sheet>
    );
}

function ShareBody({ podId, subject, lemmaName, onClose }: { podId: string; subject: ShareSubject; lemmaName: string; onClose: () => void }) {
    const router = useRouter();
    const me = useMe();
    const may = useMayManage(podId);
    const members = usePodMembers(podId);
    const reach = useReach(podId, subject, true);
    const address = useShareAddress(podId, subject, true);
    const { live, links } = useLiveLinks(podId, subject.kind === "file" ? subject.path : "", subject.kind === "file");
    const [linking, setLinking] = useState(false);
    const [copied, setCopied] = useState(false);
    useEffect(() => {
        if (!copied) return;
        const timer = setTimeout(() => setCopied(false), 1600);
        return () => clearTimeout(timer);
    }, [copied]);

    const people = members.data ?? [];
    const mine = (me.data?.email ?? "").toLowerCase();
    const alone = members.isSuccess && onlyReader(people, { email: me.data?.email ?? null, id: me.data?.id ?? null });
    const link = linkRow(subject, reach.data, lemmaName, address.own);
    const url = address.url;

    return (
        <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 18 }} keyboardShouldPersistTaps="handled">
            {may.allowed ? (
                <Row
                    label={`Add people to ${lemmaName}`}
                    role="link"
                    glyph={<RowIcon name="plus" />}
                    last
                    onPress={() => {
                        onClose();
                        router.push(peopleRoutes.add(podId) as never);
                    }}
                />
            ) : null}

            <Group
                label="Who has access"
                foot={(
                    <View style={{ gap: 6, paddingTop: 6 }}>
                        {alone ? <Say>Only you so far.</Say> : null}
                        {subject.kind === "lemma" ? null : reach.isError ? (
                            <ReadProblem said={problemOf(reach.error, "Couldn’t read who can open this.")} onRetry={() => void reach.refetch()} />
                        ) : reach.data ? (
                            <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{whoCanOpen(subject, reach.data, lemmaName)}</Text>
                        ) : null}
                    </View>
                )}
            >
                {members.isPending ? <Reading /> : null}
                {members.isError ? (
                    <ReadProblem said={isForbidden(members.error) ? `You may not see who has access to ${lemmaName}.` : `Couldn’t load who has access to ${lemmaName}.`} onRetry={() => void members.refetch()} />
                ) : null}
                {people.map((person, index) => {
                    const you = Boolean(mine) && (person.email ?? "").toLowerCase() === mine;
                    return (
                        <Row
                            key={person.id}
                            glyph={<PersonFace name={person.name} size={28} />}
                            label={you ? `${person.name} · you` : person.name}
                            hint={person.email && person.email !== person.name ? person.email : null}
                            value={accessOf(person)}
                            last={index === people.length - 1}
                        />
                    );
                })}
            </Group>

            <Group label="Link">
                <Row label={link.title} hint={link.line} glyph={<Glyph name={link.anyone ? "globe" : "lock"} size={18} tone={color.ink2} />} last />
                <View style={{ flexDirection: "row", gap: 10, paddingLeft: 38, paddingBottom: 12 }}>
                    <Button title="Send" kind="outline" height={36} size={14} grow disabled={!url} onPress={() => url && void Share.share({ message: url, url })} />
                    <Button title={copied ? "Copied" : "Copy"} kind="outline" height={36} size={14} grow disabled={!url} onPress={() => { if (url) { copyText(url); setCopied(true); } }} />
                </View>
                {address.state === "failed" ? <ReadProblem said="Couldn’t read its address." onRetry={address.retry} /> : null}
                {address.state === "none" ? <Say>There is no address to send for this yet.</Say> : null}
                {subject.kind === "file" ? (
                    <View style={{ borderTopWidth: 1, borderTopColor: color.line }}>
                        <Row
                            label="Anyone with the link"
                            hint={links.isError ? "The links could not be read just now." : liveLinksLine(live)}
                            glyph={<Glyph name="globe" size={18} tone={color.ink2} />}
                            role="button"
                            last
                            onPress={() => setLinking(true)}
                        />
                    </View>
                ) : null}
            </Group>

            {/* Over this sheet rather than instead of it: closing it comes
                back here, as the web's public-link dialog does. */}
            {subject.kind === "file" ? (
                <FileLinkSheet podId={podId} path={subject.path} title={subject.label} lemmaName={lemmaName} open={linking} onClose={() => setLinking(false)} />
            ) : null}
        </ScrollView>
    );
}
