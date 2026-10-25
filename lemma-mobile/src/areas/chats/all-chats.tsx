import { useState } from "react";
import { KeyboardAvoidingView, RefreshControl, ScrollView, View } from "react-native";
import { useQueryClient } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";
import { useCacheKey, useLemmas, useSections } from "../../data/hooks";
import { BackLink, Empty, Loading } from "../../ui/chrome";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { SearchField, Segments } from "./bits";
import { useSettled } from "./hooks";
import { LemmaChats } from "./list";
import { CHAT_FILTERS, type ChatFilter } from "./model";

/** Every chat, across Lemmas (the `/o` frame's chats, and the web's Chats
 *  page for each Lemma): what waits on the reader, then each Lemma's chats --
 *  with it, and the groups and contacts it answers in -- the busiest Lemma
 *  first, the first few of each and the rest on asking. A search asks every
 *  Lemma for its chats by title and for what was said in its shared ones. */
export function AllChatsScreen() {
    const key = useCacheKey();
    const cache = useQueryClient();
    const { lemmas, pending } = useLemmas();
    const { sections } = useSections();
    const [typed, setTyped] = useState("");
    const search = useSettled(typed, 300);
    const [filter, setFilter] = useState<ChatFilter>("all");
    const [refreshing, setRefreshing] = useState(false);
    const now = Date.now();
    /* The busiest first, as the Lemmas tab orders them; any it has not
       placed yet after. */
    const order = new Map(sections.map((section, index) => [section.lemma.id, index]));
    const ordered = [...lemmas].sort((a, b) => (order.get(a.id) ?? 999) - (order.get(b.id) ?? 999));
    const refresh = async () => {
        setRefreshing(true);
        await cache.invalidateQueries({ queryKey: [key, "chats"] });
        setRefreshing(false);
    };
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <View style={{ paddingTop: 4, paddingHorizontal: 6 }}>
                    <BackLink label="Home" />
                </View>
                <ScrollView
                    contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 28 }}
                    keyboardShouldPersistTaps="handled"
                    refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => void refresh()} tintColor={color.ink3} />}
                >
                    <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24, paddingHorizontal: 4, paddingTop: 4, paddingBottom: 10 }}>All chats</Text>
                    <View style={{ gap: 10 }}>
                        <SearchField value={typed} onChange={setTyped} placeholder="Search every chat" />
                        <Segments items={CHAT_FILTERS.map((one) => ({ id: one.id, label: one.label }))} chosen={filter} onPick={setFilter} label="Which chats" />
                    </View>
                    {pending ? <Loading /> : null}
                    {!pending && lemmas.length === 0 ? <Empty title="No chats yet." /> : null}
                    {ordered.map((lemma) => <LemmaChats key={lemma.id} lemma={lemma} filter={filter} search={search} compact now={now} />)}
                </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}
