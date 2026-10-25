import { useState } from "react";
import { KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";
import { useCacheKey, useLemma } from "../../data/hooks";
import { Loading } from "../../ui/chrome";
import { PersonFace } from "../../ui/face";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { Field, Label, SwitchRow, Trouble } from "./bits";
import { Header } from "./chat-view";
import { keepChannel, useAgents, useChatsSource, useMyId, usePeople } from "./hooks";
import { chatsRoutes } from "./routes";
import { problemOf } from "@web/channels/problem";

/** A chat shared with people in the Lemma (the web's `NewSharedChat`): a
 *  name and who is in it. The Lemma is in it from the start, answering
 *  whoever names it. Private unless the maker lets anyone in the Lemma find
 *  it and join. Nothing outside feeds it until a group is brought in. */
export function NewGroupScreen({ podId }: { podId: string }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "The Lemma";
    const people = usePeople(podId);
    const agents = useAgents(podId);
    const front = agents.data?.find((agent) => agent.front && agent.id) ?? null;
    const [title, setTitle] = useState("");
    const [chosen, setChosen] = useState<Set<string>>(new Set());
    const [open, setOpen] = useState(false);
    const others = (people.data ?? []).filter((one) => one.userId !== me);
    const make = useMutation({
        mutationFn: () => source.create(podId, {
            title: title.trim(),
            topic: null,
            members: [...chosen],
            visibility: open ? "PUBLIC" : "PRIVATE",
            agents: front ? [{ id: front.id, answers: "MENTIONED" }] : [],
        }),
        onSuccess: (made) => {
            keepChannel(cache, key, podId, made);
            router.replace(chatsRoutes.shared(podId, made.id) as never);
        },
    });
    const toggle = (userId: string) => setChosen((was) => {
        const next = new Set(was);
        if (next.has(userId)) next.delete(userId); else next.add(userId);
        return next;
    });
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <Header podId={podId} title="New group" subtitle={"With people in " + lemmaName + ". Anyone in it can ask " + lemmaName + "."} glyph={null} />
                <ScrollView contentContainerStyle={{ padding: 16, gap: 10, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                    <Label>Name</Label>
                    <Field accessibilityLabel="The group’s name" autoFocus value={title} maxLength={200} placeholder="Launch plan" onChangeText={setTitle} returnKeyType="done" />
                    <Label>{"Who is in it" + (chosen.size ? " · " + (chosen.size + 1) : "")}</Label>
                    {people.isPending ? <Loading /> : others.length === 0 ? (
                        <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>Nobody else is in {lemmaName} yet. Share {lemmaName} first, then add them here.</Text>
                    ) : others.map((one) => {
                        const on = chosen.has(one.userId);
                        return (
                            <Press key={one.userId} role="button" label={one.name} accessibilityState={{ checked: on }} onPress={() => toggle(one.userId)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4 }}>
                                <PersonFace name={one.name} size={30} />
                                <View style={{ flex: 1, minWidth: 0 }}>
                                    <Text size={15.5} numberOfLines={1}>{one.name}</Text>
                                    {one.email ? <Text size={12.5} tone={color.ink3} numberOfLines={1}>{one.email}</Text> : null}
                                </View>
                                <View style={{ width: 24, height: 24, borderRadius: 12, borderWidth: on ? 0 : 1.5, borderColor: color.line2, backgroundColor: on ? color.ink : "transparent", alignItems: "center", justifyContent: "center" }}>
                                    {on ? <Icon name="check" size={14} tone={color.onInk} width={2.2} /> : null}
                                </View>
                            </Press>
                        );
                    })}
                    <SwitchRow label={"Anyone in " + lemmaName + " can find it and join"} note={open ? "Everyone in " + lemmaName + " can read it." : "Only the people in it see it."} value={open} onChange={setOpen} />
                    <Trouble>{make.isError ? problemOf(make.error, "Couldn’t start the group.") : null}</Trouble>
                    <View style={{ flexDirection: "row", gap: 8, justifyContent: "flex-end", paddingTop: 6 }}>
                        <Button title="Cancel" kind="outline" onPress={() => router.back()} />
                        <Button title="Start the group" busy={make.isPending} disabled={!title.trim()} onPress={() => make.mutate()} />
                    </View>
                </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
