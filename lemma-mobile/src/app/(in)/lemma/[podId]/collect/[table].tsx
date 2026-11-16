import { useState } from "react";
import { Platform, ScrollView, Share, View } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { ForbiddenError } from "lemma-sdk";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemma } from "../../../../../data/hooks";
import { NEW_CHAT, routes } from "../../../../../data/routes";
import { useFeature } from "../../../../../session/features";
import { BackLink, Loading, Problem as Failed } from "../../../../../ui/chrome";
import { Icon } from "../../../../../ui/icons";
import { Button, Press } from "../../../../../ui/press";
import { Sheet } from "../../../../../ui/sheet";
import { Text } from "../../../../../ui/text";
import { color } from "../../../../../ui/tokens";
import { AskSheet, GroupHead, Quiet } from "../../../../../areas/tables/bits";
import { useMakeWebChat, useOpening, useSaveOpening, useWebChats } from "../../../../../areas/tables/hooks";
import type { NewWebWidget } from "@web/data/contacts";
import {
    audienceChoices, columnQuestion, customFormAsk, doorFor, formEmbed, formLink, htmlFormSnippet, openingProblem, startingColumns, widgetAnswerFor,
    type Audience, type TableOpening,
} from "@web/data/public-rows";
import { readableName } from "@web/library/reading";

const MONO = Platform.select({ ios: "Menlo", default: "monospace" });

/** Collect responses (`lemma-frontend` `CollectResponses`): a table's rows,
 *  added by people outside the Lemma. There is no form to build: opening the
 *  table says who may add rows and which columns they may write, and any page
 *  can then be the form, the one Lemma hosts on the Lemma's web chat, a
 *  website with the script, or an app the Lemma designs. Behind the
 *  `contacts` flag, as on the web. */
export default function CollectResponses() {
    const router = useRouter();
    const params = useLocalSearchParams<{ podId: string; table: string }>();
    const podId = String(params.podId);
    const table = String(params.table);
    const lemma = useLemma(podId);
    const on = useFeature("contacts");
    const opening = useOpening(podId, table);
    const chats = useWebChats(podId);
    const save = useSaveOpening(podId, table);
    const make = useMakeWebChat(podId);
    const [audience, setAudience] = useState<Audience | "off" | null>(null);
    const [columns, setColumns] = useState<string[] | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [made, setMade] = useState<NewWebWidget | null>(null);
    const [own, setOwn] = useState(false);
    const [asking, setAsking] = useState(false);
    const who = lemma?.name ?? "the Lemma";
    const title = readableName(table);

    const data = opening.data;
    const chosenAudience: Audience | "off" = audience ?? data?.audience ?? "anyone";
    const chosenColumns = columns ?? (data ? startingColumns(data) : []);
    const isOpen = Boolean(data?.audience);
    /* Read only once the chats have loaded: a guess made while they load
       would offer to make a chat the Lemma already has. */
    const door = data?.audience && chats.data ? doorFor(chats.data, data.audience) : null;

    const submit = () => {
        if (!data) return;
        const wrong = chosenAudience === "off" ? null : openingProblem(data, chosenColumns);
        setProblem(wrong);
        if (wrong) return;
        save.mutate({ to: chosenAudience, columns: chosenColumns }, {
            onSuccess: () => { setAudience(null); setColumns(null); },
            onError: (error) => setProblem(error instanceof ForbiddenError ? "Only someone who can change this table can open it." : error instanceof Error && error.message ? error.message : "Couldn’t save it. Try again."),
        });
    };

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <View style={{ paddingTop: 4, paddingHorizontal: 6, alignSelf: "flex-start" }}>
                <BackLink label={title} />
            </View>
            <ScrollView contentContainerStyle={{ paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                <View style={{ paddingHorizontal: 20, paddingTop: 4, gap: 4 }}>
                    <Text accessibilityRole="header" size={24} weight="medium" style={{ letterSpacing: -0.24 }}>Collect responses</Text>
                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>People outside {who} add rows to {title}, through a form.</Text>
                </View>
                <View style={{ paddingHorizontal: 16 }}>
                    {!on ? (
                        <Text size={15.5} tone={color.ink2} style={{ paddingHorizontal: 4, paddingTop: 16, lineHeight: 22 }}>Collecting responses is not on for this account yet.</Text>
                    ) : opening.isPending || chats.isPending ? (
                        <Loading />
                    ) : !data ? (
                        <Failed error={new Error("Couldn’t read this table.")} onRetry={() => void opening.refetch()} />
                    ) : !chats.data ? (
                        <Failed error={new Error(`Couldn’t read ${who}’s web chats.`)} onRetry={() => void chats.refetch()} />
                    ) : data.perUser ? (
                        <Text size={15.5} tone={color.ink2} style={{ paddingHorizontal: 4, paddingTop: 16, lineHeight: 22 }}>Each member sees only their own rows in this table, so it can’t take rows from outside. Make a shared table for responses.</Text>
                    ) : (
                        <>
                            <GroupHead text="Who can answer" />
                            {audienceChoices().map((choice) => {
                                const picked = chosenAudience === choice.value;
                                return (
                                    <Press key={choice.value} role="radio" label={choice.label} accessibilityState={{ selected: picked, disabled: save.isPending }} disabled={save.isPending} onPress={() => setAudience(choice.value)} style={{ flexDirection: "row", alignItems: "center", gap: 14, paddingVertical: 10, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
                                        <View style={{ width: 20, height: 20, borderRadius: 10, borderWidth: picked ? 6 : 1.5, borderColor: picked ? color.ink : color.ink3 }} />
                                        <View style={{ flex: 1, gap: 2 }}>
                                            <Text size={15.5}>{choice.label}</Text>
                                            <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{choice.note}</Text>
                                        </View>
                                    </Press>
                                );
                            })}

                            {chosenAudience !== "off" ? <Columns opening={data} chosen={chosenColumns} disabled={save.isPending} onChange={setColumns} /> : null}

                            {problem ? <Text accessibilityRole="alert" size={14} tone={color.amber} style={{ paddingHorizontal: 4, paddingTop: 12 }}>{problem}</Text> : null}
                            <View style={{ paddingTop: 16, flexDirection: "row" }}>
                                <Button
                                    grow
                                    height={46}
                                    busy={save.isPending}
                                    title={chosenAudience === "off" ? "Stop taking responses" : isOpen ? "Save" : "Start taking responses"}
                                    onPress={submit}
                                />
                            </View>

                            {data.audience && !door ? (
                                <View style={{ paddingTop: 20, gap: 10 }}>
                                    <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, lineHeight: 21 }}>
                                        People reach a form through {who}’s web chat, and none of its chats answers {data.audience === "contacts" ? "people who confirm their email" : "anyone"} yet.
                                        {chats.data.length ? " Switch one on in Contacts, or make one for this form." : " Make one for this form."}
                                    </Text>
                                    {make.isError ? <Text accessibilityRole="alert" size={14} tone={color.amber} style={{ paddingHorizontal: 4 }}>Couldn’t make the chat. Try again.</Text> : null}
                                    <View style={{ flexDirection: "row" }}>
                                        <Button title={make.isPending ? "Making…" : "Make a web chat for it"} kind="outline" busy={make.isPending} onPress={() => make.mutate({ name: "Website", answer: widgetAnswerFor(data.audience ?? "anyone") }, { onSuccess: setMade })} />
                                    </View>
                                </View>
                            ) : null}

                            {isOpen && door && data.audience ? (
                                <>
                                    <GroupHead text="Share the form" />
                                    <Code text={formLink(door.pageUrl, table)} />
                                    <View style={{ flexDirection: "row", gap: 8, paddingTop: 8 }}>
                                        <Button title="Share the link" grow onPress={() => void Share.share({ message: formLink(door.pageUrl, table) })} />
                                        <Button title="Open the page" kind="outline" grow onPress={() => void WebBrowser.openBrowserAsync(formLink(door.pageUrl, table))} />
                                    </View>
                                    <GroupHead text="Or put it on your website" />
                                    <Code text={formEmbed(door.embed, table)} />
                                    <View style={{ flexDirection: "row", paddingTop: 8 }}>
                                        <Button title="Share the code" kind="outline" onPress={() => void Share.share({ message: formEmbed(door.embed, table) })} />
                                    </View>
                                    <View style={{ height: 12 }} />
                                    <Quiet text={own ? "Hide your own HTML" : "Use your own HTML"} onPress={() => setOwn(!own)} tone={color.ink} />
                                    {own ? (
                                        <View style={{ gap: 8 }}>
                                            <Text size={13.5} tone={color.ink3} style={{ paddingHorizontal: 4, lineHeight: 19 }}>Any form with these field names works; the table still decides what is written.</Text>
                                            <Code text={htmlFormSnippet(door.embed, table, data.offered.filter((column) => data.columns.includes(column.name)))} />
                                            <View style={{ flexDirection: "row" }}>
                                                <Button title="Share the HTML" kind="outline" onPress={() => void Share.share({ message: htmlFormSnippet(door.embed, table, data.offered.filter((column) => data.columns.includes(column.name))) })} />
                                            </View>
                                        </View>
                                    ) : null}
                                    <View style={{ height: 8 }} />
                                    <Quiet text={`Ask ${who} for a custom design`} onPress={() => setAsking(true)} tone={color.ink} />
                                </>
                            ) : null}
                        </>
                    )}
                </View>
            </ScrollView>

            <AskSheet open={asking} onClose={() => setAsking(false)} name={who} start={customFormAsk(table)} onSend={(text) => router.push(routes.chat(podId, NEW_CHAT, text) as never)} />
            {made ? <SecretSheet widget={made} onDone={() => setMade(null)} /> : null}
        </SafeAreaView>
    );
}

function Columns({ opening, chosen, disabled, onChange }: { opening: TableOpening; chosen: string[]; disabled: boolean; onChange: (next: string[]) => void }) {
    if (!opening.offered.length) return <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, paddingTop: 16 }}>This table has nothing a person outside could fill in.</Text>;
    return (
        <>
            <GroupHead text="What they fill in" />
            {opening.offered.map((column) => {
                const ticked = column.required || chosen.includes(column.name);
                return (
                    <Press
                        key={column.name}
                        label={columnQuestion(column)}
                        accessibilityRole="checkbox"
                        accessibilityState={{ checked: ticked, disabled: disabled || column.required }}
                        disabled={disabled || column.required}
                        onPress={() => onChange(ticked ? chosen.filter((name) => name !== column.name) : [...chosen, column.name])}
                        style={{ flexDirection: "row", alignItems: "center", gap: 14, paddingVertical: 10, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}
                    >
                        <View style={{ width: 22, height: 22, borderRadius: 7, borderWidth: ticked ? 0 : 1.5, borderColor: color.ink3, backgroundColor: ticked ? (column.required ? color.ink3 : color.ink) : "transparent", alignItems: "center", justifyContent: "center" }}>
                            {ticked ? <Icon name="check" size={15} width={2.4} tone={color.onInk} /> : null}
                        </View>
                        <View style={{ flex: 1, gap: 2 }}>
                            <Text size={15.5}>{columnQuestion(column)}</Text>
                            <Text size={13} tone={color.ink3}>{column.name}{column.required ? " · the table needs it" : ""}</Text>
                        </View>
                    </Press>
                );
            })}
            <Text size={13} tone={color.ink3} style={{ paddingHorizontal: 4, paddingTop: 8, lineHeight: 18 }}>A column’s description is the question people see. Edit it in the table to reword it.</Text>
        </>
    );
}

/** Something to paste somewhere else, as it will be pasted. */
function Code({ text }: { text: string }) {
    return (
        <View style={{ backgroundColor: color.wash, borderRadius: 12, padding: 12 }}>
            <Text selectable size={13} style={{ fontFamily: MONO, lineHeight: 19 }}>{text}</Text>
        </View>
    );
}

/** A new chat's signing secret, the one time it is shown. Only saying it
 *  has been kept closes this: a stray tap outside would lose it for good. */
function SecretSheet({ widget, onDone }: { widget: NewWebWidget; onDone: () => void }) {
    return (
        <Sheet open onClose={() => undefined} title={`${widget.name}: keep the signing secret now`} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, gap: 12, paddingBottom: 4 }}>
                <Text size={15} style={{ lineHeight: 21 }}>Your site’s server uses this to sign in its own customers, so they are answered as contacts. It is shown only now; keep it off web pages.</Text>
                <Code text={widget.signingSecret} />
                <View style={{ flexDirection: "row" }}>
                    <Button title="Share the secret" kind="outline" onPress={() => void Share.share({ message: widget.signingSecret })} />
                </View>
                <Text size={15} style={{ lineHeight: 21 }}>Then put this on the page:</Text>
                <Code text={widget.embed} />
                <View style={{ flexDirection: "row", gap: 8 }}>
                    <Button title="Share the code" kind="outline" onPress={() => void Share.share({ message: widget.embed })} />
                </View>
                <Button title="I’ve kept the secret" height={46} onPress={onDone} />
            </ScrollView>
        </Sheet>
    );
}
