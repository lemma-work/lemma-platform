import { useRef, useState } from "react";
import { Alert, KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import * as Crypto from "expo-crypto";
import * as FileSystem from "expo-file-system/legacy";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";
import { useCacheKey, useLemma } from "../../data/hooks";
import { shareFile } from "../../data/io";
import { Loading } from "../../ui/chrome";
import { PersonFace } from "../../ui/face";
import { Button } from "../../ui/press";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { Choices, Field, Label, Rule, TextAction, Trouble } from "./bits";
import { Header } from "./chat-view";
import { chatKeys, useChatsSource, useMaySeeContacts } from "./hooks";
import { followUpSaid, isForbidden } from "./model";
import { chatsRoutes } from "./routes";
import { contactName, handleText, outreachProblem, vouchedBy, waitsOnTheSite, writableHandles, type Contact } from "@web/data/contacts";

/** One contact (the web's `ContactSheet`, as a screen): how they are known
 *  and who vouched for it, their name, a message written to them first,
 *  their chat, what the Lemma holds about them, and forgetting them. */
export function ContactScreen({ podId, contactId }: { podId: string; contactId: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const lemma = useLemma(podId);
    const maySee = useMaySeeContacts(podId);
    const contacts = useQuery({ queryKey: chatKeys.contacts(key, podId), queryFn: () => source.contacts(podId), enabled: maySee, staleTime: 30_000 });
    const contact = contacts.data?.find((one) => one.id === contactId) ?? null;
    const lemmaName = lemma?.name ?? "The Lemma";
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <Header podId={podId} title={contact ? contactName(contact) : "Contact"} subtitle={"A contact of " + lemmaName} glyph={null} />
                {contacts.isPending ? <Loading /> : !contact ? (
                    <View style={{ padding: 20 }}><Trouble>{maySee ? "They are not one of " + lemmaName + "’s contacts any more." : "Only " + lemmaName + "’s editors and admins see its contacts."}</Trouble></View>
                ) : <Body podId={podId} contact={contact} lemmaName={lemmaName} />}
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

function Body({ podId, contact, lemmaName }: { podId: string; contact: Contact; lemmaName: string }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.contacts(key, podId) });
    const [name, setName] = useState(contact.name ?? "");
    const [message, setMessage] = useState("");
    const [said, setSaid] = useState<string | null>(null);
    const [sentTo, setSentTo] = useState<string | null>(null);
    const handles = writableHandles(contact);
    const [handleAt, setHandleAt] = useState(0);
    const lineId = useRef<string | null>(null);
    const rename = useMutation({ mutationFn: () => source.renameContact(podId, contact.id, name.trim() || null), onSuccess: reread });
    const forget = useMutation({
        mutationFn: () => source.forgetContact(podId, contact.id),
        onSuccess: () => { reread(); router.back(); },
    });
    const followUp = useMutation({
        mutationFn: () => {
            const handle = handles[handleAt] ?? handles[0];
            return source.followUp(podId, contact.id, message.trim(), handle, (lineId.current ??= Crypto.randomUUID()));
        },
        onSuccess: (sent) => { lineId.current = null; setMessage(""); setSentTo(sent.channelId); setSaid(followUpSaid(sent)); },
        onError: (error) => setSaid(outreachProblem(error)),
    });
    const exported = useMutation({
        mutationFn: async () => {
            const json = await source.exportContact(podId, contact.id);
            const file = `${FileSystem.cacheDirectory}${(contactName(contact).replace(/[^\w.-]+/g, "-") || "contact")}.json`;
            await FileSystem.writeAsStringAsync(file, json);
            await shareFile(file, "application/json");
        },
    });
    const chat = useMutation({
        mutationFn: async () => {
            const found = await source.list(podId, { kind: "CONTACT", archived: false, contactId: contact.id, limit: 1 });
            return found.items[0] ?? null;
        },
        onSuccess: (channel) => { if (channel) router.push(chatsRoutes.shared(podId, channel.id) as never); },
    });
    const handle = handles[handleAt] ?? handles[0];
    return (
        <ScrollView contentContainerStyle={{ padding: 16, gap: 10, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <PersonFace name={contactName(contact)} size={44} />
                <View style={{ flex: 1 }}>
                    <Text size={18} weight="medium">{contactName(contact)}</Text>
                    <Text size={13} tone={color.ink3}>Since {new Date(contact.createdAt).toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" })}</Text>
                </View>
            </View>
            <Label>How they are known</Label>
            {contact.handles.map((one) => (
                <View key={one.kind + one.value} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 34, paddingHorizontal: 4 }}>
                    <Text size={15} style={{ flex: 1 }} selectable>{handleText(one)}</Text>
                    <Text size={12.5} tone={color.ink3}>{vouchedBy(one)}</Text>
                </View>
            ))}
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 4 }}>
                <Button title={chat.isPending ? "Finding it…" : "Open their chat"} kind="outline" height={38} size={14} busy={chat.isPending} onPress={() => chat.mutate()} />
            </View>
            {chat.isSuccess && !chat.data ? <Text size={13.5} tone={color.ink2}>They have no chat yet: it starts when they first write.</Text> : null}
            <Trouble>{chat.isError ? "Couldn’t find their chat. Try again." : null}</Trouble>
            <Rule />
            <Label>Name</Label>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Field accessibilityLabel="How to address them" value={name} maxLength={255} placeholder="How to address them" onChangeText={setName} style={{ flex: 1 }} />
                <Button title="Save" kind="outline" height={40} size={14} busy={rename.isPending} disabled={name === (contact.name ?? "")} onPress={() => rename.mutate()} />
            </View>
            <Trouble>{rename.isError ? "Couldn’t rename them. Try again." : null}</Trouble>
            <Rule />
            <Label>Write to them first</Label>
            {handles.length > 0 ? (
                <View style={{ gap: 8 }}>
                    <Field accessibilityLabel="Write to them first" multiline value={message} maxLength={4000} placeholder={"From " + lemmaName} onChangeText={(text) => { lineId.current = null; setMessage(text); }} style={{ minHeight: 88, textAlignVertical: "top" }} />
                    {handles.length > 1 ? <Choices label="On" options={handles.map((one, index) => ({ value: String(index), label: handleText(one) }))} value={String(handleAt)} onPick={(next) => { lineId.current = null; setHandleAt(Number(next)); }} /> : null}
                    <Text size={12.5} tone={color.ink3} style={{ lineHeight: 17 }}>
                        {handle && waitsOnTheSite(handle) ? "It waits in their chat on your website, and they read it when they come back." : "Email any time; WhatsApp only within a day of their last message there."} It goes in their chat, where you can see whether it went.
                    </Text>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                        <Button title="Send" height={38} size={14} busy={followUp.isPending} disabled={!message.trim()} onPress={() => { setSaid(null); followUp.mutate(); }} />
                        {sentTo ? <TextAction label="Open their chat" tone={color.ink} onPress={() => router.push(chatsRoutes.shared(podId, sentTo) as never)} /> : null}
                    </View>
                    {said ? <Text size={13.5} tone={followUp.isError ? color.amber : color.ink2}>{said}</Text> : null}
                </View>
            ) : <Text size={14} tone={color.ink2}>There is no address or number to write to them on. They can be answered when they next write.</Text>}
            <Rule />
            <TextAction label={"Export what " + lemmaName + " holds"} busy={exported.isPending} onPress={() => exported.mutate()} />
            <Trouble>{exported.isError ? (isForbidden(exported.error) ? "Only an admin of " + lemmaName + " can export a contact." : "Couldn’t export. Try again.") : null}</Trouble>
            <TextAction label="Forget this contact" tone={color.amber} busy={forget.isPending} onPress={() => Alert.alert("Forget " + contactName(contact) + "?", "Every conversation with them goes too. This cannot be undone.", [
                { text: "Keep", style: "cancel" },
                { text: "Forget", style: "destructive", onPress: () => forget.mutate() },
            ])} />
            <Trouble>{forget.isError ? (isForbidden(forget.error) ? "Only an admin of " + lemmaName + " can forget a contact." : "Couldn’t forget them. Try again.") : null}</Trouble>
        </ScrollView>
    );
}
