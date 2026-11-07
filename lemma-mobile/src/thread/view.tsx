import { createContext, memo, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { filesRoutes } from "../areas/files/routes";
import { routes } from "../data/routes";
import type { Lemma } from "../data/types";
import { LemmaFace } from "../ui/face";
import { Icon } from "../ui/icons";
import { Markdown, StreamingMarkdown } from "../ui/markdown";
import { Press } from "../ui/press";
import { Sheet } from "../ui/sheet";
import { Num, Text } from "../ui/text";
import { color } from "../ui/tokens";
import { ToolCardView } from "./cards";
import { copyText } from "./clipboard";
import { Glyph, type GlyphName } from "./glyphs";
import { InteractionCard } from "./interaction";
import { useLook } from "./look";
import type { Noted } from "@web/thread/memory-notes";
import { filesOfMessage, isPicture, withPhotoRuns } from "./message-files";
import { FileView, PhotoRun, ResourceCard } from "./resources";
import { RunLine } from "./run-line";
import { liveNote, relayedBy, spanOf, type HumanMessage, type Streaming, type Turn } from "@web/thread/turns";
import type { Resolve } from "./use-chat";

/** A transcript, for a phone: the web's `turn-row.tsx` and `transcript.tsx`
 *  rows. A person's message on the right, the files they sent drawn as
 *  files; the Lemma's turns with its face in a gutter, what it said, showed
 *  and asked, in the order it happened, and under them one line for the
 *  work: the step it is on while it runs, how long and how many steps once
 *  it is done (`run-line.tsx`). A reply streaming in arrives word by word.
 *  Holding a message down offers what can be done with it. */

/* ── holding a message down ─────────────────────────────────────────────── */

interface MenuAction {
    label: string;
    glyph: GlyphName;
    run: () => void;
}

const MenuContext = createContext<(title: string, actions: MenuAction[]) => void>(() => undefined);

/** The one sheet a long press opens, for every message in the thread. */
export function MessageMenu({ children }: { children: ReactNode }) {
    const [open, setOpen] = useState<{ title: string; actions: MenuAction[] } | null>(null);
    const show = useCallback((title: string, actions: MenuAction[]) => setOpen({ title, actions }), []);
    return (
        <MenuContext.Provider value={show}>
            {children}
            <Sheet open={open !== null} onClose={() => setOpen(null)} title={open?.title}>
                {(open?.actions ?? []).map((action) => (
                    <Press
                        key={action.label}
                        label={action.label}
                        onPress={() => {
                            setOpen(null);
                            action.run();
                        }}
                        style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: 52, paddingHorizontal: 20 }}
                    >
                        <Glyph name={action.glyph} size={19} tone={color.ink} />
                        <Text size={16}>{action.label}</Text>
                    </Press>
                ))}
            </Sheet>
        </MenuContext.Provider>
    );
}

function useMenu() {
    return useContext(MenuContext);
}

function copyAction(text: string): MenuAction {
    return { label: "Copy", glyph: "copy", run: () => copyText(text) };
}

/** Where the menu says it came from: the start of the message. */
function headOf(text: string): string {
    const line = text.replace(/\s+/g, " ").trim();
    return line.length > 60 ? line.slice(0, 59) + "…" : line;
}

/* ── lines between turns ────────────────────────────────────────────────── */

export function DayChip({ day }: { day: string }) {
    return (
        <View accessibilityRole="header" style={{ alignSelf: "center", backgroundColor: color.wash, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3 }}>
            <Text size={12} tone={color.ink3}>{day}</Text>
        </View>
    );
}

/** Text that arrived rather than was asked for -- a notice from a schedule,
 *  a heads-up from the platform -- held to a few lines until asked for. */
function Clamped({ text, lines = 3 }: { text: string; lines?: number }) {
    const look = useLook();
    const [open, setOpen] = useState(false);
    const [height, setHeight] = useState(0);
    const limit = lines * 19;
    const clipped = height > limit + 2;
    return (
        <View>
            <View style={{ maxHeight: open ? undefined : limit, overflow: "hidden" }}>
                <View onLayout={(event) => setHeight(event.nativeEvent.layout.height)}>
                    <Markdown text={text} size={13.5} copyable={false} />
                </View>
            </View>
            {clipped ? (
                <Press label={open ? "Show less" : "Show more"} accessibilityState={{ expanded: open }} onPress={() => setOpen((was) => !was)} style={{ alignSelf: "flex-start", minHeight: 28, justifyContent: "center" }}>
                    <Text size={12.5} tone={look.ink}>{open ? "Show less" : "Show more"}</Text>
                </Press>
            ) : null}
        </View>
    );
}

function Notice({ text }: { text: string }) {
    return (
        <View accessibilityRole="summary" style={{ flexDirection: "row", gap: 8, paddingVertical: 8, paddingHorizontal: 12, borderRadius: 12, backgroundColor: color.chrome }}>
            <View style={{ paddingTop: 2 }}><Icon name="bell" size={14} tone={color.ink3} /></View>
            <View style={{ flex: 1 }}><Clamped text={text} /></View>
        </View>
    );
}

/* ── a person's message ─────────────────────────────────────────────────── */

function Chip({ text, glyph, onPress }: { text: string; glyph?: GlyphName; onPress?: () => void }) {
    const look = useLook();
    const inside = (
        <>
            {glyph ? <Glyph name={glyph} size={11} tone={color.ink2} /> : null}
            <Text size={12} tone={onPress ? look.ink : color.ink2}>{text}</Text>
        </>
    );
    const style = { flexDirection: "row" as const, alignItems: "center" as const, gap: 4, borderRadius: 999, backgroundColor: color.wash, paddingHorizontal: 8, paddingVertical: 2 };
    return onPress ? <Press role="link" label={text} onPress={onPress} hitSlop={8} style={style}>{inside}</Press> : <View style={style}>{inside}</View>;
}

/** Yours on the far side; what came in from the people outside, or from
 *  another Lemma, on the near side under its name. One still going says so
 *  where its time would be, and one the server refused stays where it was
 *  written with Retry and Edit under it. */
function Human({ message, name, podId, outsiders, onRetry, onEdit }: {
    message: HumanMessage;
    name: string;
    podId: string;
    outsiders?: string;
    onRetry?: (id: string) => void;
    onEdit?: (id: string) => void;
}) {
    const router = useRouter();
    const look = useLook();
    const menu = useMenu();
    const { text: said, files } = useMemo(() => filesOfMessage(message.text), [message.text]);
    const pictures = files.filter((one) => isPicture(one.path)).map((one) => one.path);
    const others = files.filter((one) => !isPicture(one.path));
    const relayed = relayedBy(message);
    const guest = Boolean(outsiders && message.from) || Boolean(relayed);
    const at = message.pending === "sending" ? "Sending…" : message.pending === "failed" ? "Not sent" : message.at;
    const who = relayed ? relayed.name : guest ? message.from : null;
    const failed = message.pending === "failed";
    const actions: MenuAction[] = [copyAction(message.text)];
    if (failed && onRetry) actions.push({ label: "Retry", glyph: "move", run: () => onRetry(message.id) });
    if (failed && onEdit) actions.push({ label: "Edit", glyph: "edit", run: () => onEdit(message.id) });
    const place = relayed?.place ?? null;
    return (
        <View style={{ alignSelf: guest ? "flex-start" : "flex-end", maxWidth: "86%", alignItems: guest ? "flex-start" : "flex-end", gap: 5 }}>
            {who || message.note || relayed || (guest && outsiders) ? (
                <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, justifyContent: guest ? "flex-start" : "flex-end" }}>
                    {who ? <Text size={13} weight="medium">{who}</Text> : null}
                    {relayed ? <Chip text={relayed.chip} onPress={place ? () => router.push(routes.chat(place.podId, place.conversationId) as never) : undefined} /> : null}
                    {guest && !relayed && outsiders ? <Chip text={"Not in " + outsiders} /> : null}
                    {message.note ? <Chip text={"Note to " + name} glyph="lock" /> : null}
                </View>
            ) : null}
            {pictures.length > 0 ? (
                <View style={{ width: 260, maxWidth: "100%", opacity: message.pending === "sending" ? 0.7 : 1 }}>
                    <PhotoRun podId={podId} paths={pictures} label={guest ? "Photos" : "Your photos"} />
                </View>
            ) : null}
            {others.map((one) => (
                <View key={one.path} style={{ width: 280, maxWidth: "100%" }}>
                    <FileView podId={podId} path={one.path} />
                </View>
            ))}
            {said ? (
                <Press
                    label={(guest ? (who ?? "Someone") + ": " : "You: ") + said}
                    accessibilityHint="Hold for more"
                    onLongPress={() => menu(headOf(said), actions)}
                    delayLongPress={350}
                    style={{
                        backgroundColor: guest ? color.paper : color.wash,
                        borderWidth: guest ? 1 : 0,
                        borderColor: color.line2,
                        borderTopLeftRadius: guest ? 6 : 18,
                        borderTopRightRadius: guest ? 18 : 18,
                        borderBottomLeftRadius: 18,
                        borderBottomRightRadius: guest ? 18 : 6,
                        paddingVertical: 9,
                        paddingHorizontal: 14,
                        opacity: message.pending === "sending" ? 0.7 : 1,
                    }}
                >
                    <Markdown text={said} copyable={false} />
                </Press>
            ) : null}
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                {at ? (/\d/.test(at) ? <Num size={12.5} tone={color.ink2}>{at}</Num> : <Text size={12.5} tone={failed ? color.amber : color.ink2}>{at}</Text>) : null}
                {failed && onRetry ? (
                    <Press label="Retry sending it" onPress={() => onRetry(message.id)} hitSlop={10} style={{ minHeight: 28, justifyContent: "center" }}>
                        <Text size={13} tone={look.ink}>Retry</Text>
                    </Press>
                ) : null}
                {failed && onEdit ? (
                    <Press label="Edit it first" onPress={() => onEdit(message.id)} hitSlop={10} style={{ minHeight: 28, justifyContent: "center" }}>
                        <Text size={13} tone={look.ink}>Edit</Text>
                    </Press>
                ) : null}
            </View>
        </View>
    );
}

/* ── the Lemma's turn ───────────────────────────────────────────────────── */

/** What the Lemma said, on its own surface: four things said while it
 *  worked are four things said. */
function Speech({ text, streaming = false }: { text: string; streaming?: boolean }) {
    const menu = useMenu();
    return (
        <Press
            label={text}
            accessibilityHint="Hold for more"
            onLongPress={() => menu(headOf(text), [copyAction(text)])}
            delayLongPress={350}
            style={{ alignSelf: "stretch", borderWidth: 1, borderColor: color.line, borderRadius: 16, paddingVertical: 10, paddingHorizontal: 14, backgroundColor: color.paper }}
        >
            {streaming ? <StreamingMarkdown text={text} /> : <Markdown text={text} />}
        </Press>
    );
}

/** "Support noted this · Pricing": the platform saying the Lemma wrote a
 *  note, from the write itself. Each topic opens the note in Files. */
function NotedLine({ name, noted, podId }: { name: string; noted: Noted[]; podId: string }) {
    const router = useRouter();
    const look = useLook();
    const mine = noted.every((one) => one.private);
    return (
        <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
            <Glyph name="memory" size={13} tone={color.ink3} />
            <Text size={13} tone={color.ink3}>{name} noted this{mine ? " for you" : ""}</Text>
            {noted.map((one) => (
                <Press key={one.path} role="link" label={`Open the note ${one.topic}`} onPress={() => router.push(filesRoutes.file(podId, one.path) as never)} hitSlop={8}>
                    <Text size={13} tone={look.ink}>· {one.topic}</Text>
                </Press>
            ))}
        </View>
    );
}

function RuntimeLine({ text }: { text: string }) {
    return (
        <View accessibilityRole="text" style={{ flexDirection: "row", gap: 6, alignItems: "flex-start" }}>
            <View style={{ paddingTop: 2 }}><Glyph name="terminal" size={13} tone={color.ink3} /></View>
            <Text size={13} tone={color.ink3} style={{ flex: 1, lineHeight: 18 }}>{text}</Text>
        </View>
    );
}

export function Reply({ lemma, at, children }: { lemma: Lemma; at?: string; children: ReactNode }) {
    return (
        <View style={{ flexDirection: "row", gap: 10 }}>
            <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={26} />
            <View style={{ flex: 1, minWidth: 0, alignItems: "flex-start", gap: 8 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, height: 26 }}>
                    <Text size={14.5} weight="medium">{lemma.name}</Text>
                    {at ? <Num size={12} tone={color.ink3}>{at}</Num> : null}
                </View>
                {children}
            </View>
        </View>
    );
}

export interface TurnProps {
    turn: Turn;
    /** The run in flight, handed to the one turn it is drawn into. */
    streaming: Streaming | null;
    lemma: Lemma;
    conversationId: string | null;
    outsiders?: string;
    /** The pause docked above the composer, skipped here so it is not two
     *  live cards. An open pause is always the last thing in a chat. */
    dockedId?: string | null;
    runEnded: boolean;
    resumesOnAnswer: boolean;
    onResolve: Resolve;
    onRetrySend: (id: string) => void;
    onEditSend: (id: string) => void;
    onCompose: (text: string, newConversation: boolean) => void;
}

/** One turn: what was asked, the work, and what came back. Memoised: the
 *  thread redraws for every few tokens of a reply, and only the turn the
 *  reply is drawn into has changed (`turn-identity.ts` keeps the rest). */
export const TurnRow = memo(function TurnRow({ turn, streaming, lemma, conversationId, outsiders, dockedId, runEnded, resumesOnAnswer, onResolve, onRetrySend, onEditSend, onCompose }: TurnProps) {
    const merging = Boolean(streaming);
    const notes = useMemo(() => (streaming ? [...turn.notes, ...liveNote(streaming, turn.notes)] : turn.notes), [turn.notes, streaming]);
    const drawn = useMemo(() => withPhotoRuns(turn.items), [turn.items]);
    const spoke = turn.items.find((item) => item.kind === "text");
    const podId = lemma.id;
    return (
        <View style={{ gap: 14 }}>
            {turn.day ? <DayChip day={turn.day} /> : null}
            {turn.notice ? <Notice text={turn.notice} /> : null}
            {turn.human ? <Human message={turn.human} name={lemma.name} podId={podId} outsiders={outsiders} onRetry={onRetrySend} onEdit={onEditSend} /> : null}
            {notes.length > 0 || turn.items.length > 0 || merging || Boolean(turn.runtimeNotes?.length) ? (
                <Reply lemma={lemma} at={spoke?.kind === "text" ? spoke.at : undefined}>
                    {drawn.map((item) => {
                        switch (item.kind) {
                            case "photos":
                                return <PhotoRun key={item.id} podId={podId} paths={item.paths} />;
                            case "text":
                                return <Speech key={item.id} text={item.text} />;
                            case "resource":
                                return <ResourceCard key={item.id} resource={item.resource} podId={podId} conversationId={conversationId} toolCallId={item.toolCallId} onCompose={onCompose} />;
                            case "plan":
                                return (
                                    <View key={item.id} style={{ alignSelf: "stretch", borderWidth: 1, borderColor: color.line, borderRadius: 14, padding: 12, gap: 6 }}>
                                        {item.steps.map((step, index) => (
                                            <View key={index} style={{ flexDirection: "row", gap: 8, alignItems: "flex-start" }}>
                                                <Icon name={step.status === "completed" ? "check" : "forward"} size={14} width={2} tone={step.status === "completed" ? color.ink : step.status === "in_progress" ? color.violet : color.ink3} />
                                                <Text size={14} tone={step.status === "completed" ? color.ink2 : color.ink} style={{ flex: 1 }}>{step.step}</Text>
                                            </View>
                                        ))}
                                    </View>
                                );
                            case "tool-card":
                                if (item.id === dockedId) return null;
                                return (
                                    <View key={item.id} style={{ alignSelf: "stretch" }}>
                                        <ToolCardView card={item.card} podId={podId} conversationId={conversationId} toolCallId={item.toolCallId} />
                                    </View>
                                );
                            case "interaction":
                                if (item.interaction.id === dockedId) return null;
                                return <InteractionCard key={item.id} interaction={item.interaction} name={lemma.name} onResolve={onResolve} runEnded={runEnded} resumesOnAnswer={resumesOnAnswer} />;
                        }
                    })}
                    {streaming?.text ? <Speech text={streaming.text} streaming /> : null}
                    <RunLine notes={notes} live={merging} writing={Boolean(streaming?.text)} span={spanOf(turn.startedAtMs, turn.endedAtMs)} name={lemma.name} podId={podId} conversationId={conversationId} />
                    {turn.noted && turn.noted.length > 0 ? <NotedLine name={lemma.name} noted={turn.noted} podId={podId} /> : null}
                    {turn.runtimeNotes?.map((note) => <RuntimeLine key={note} text={note} />)}
                </Reply>
            ) : null}
        </View>
    );
});

/** A run with no turn to be drawn into yet. */
export function LiveReply({ lemma, streaming, podId, conversationId }: { lemma: Lemma; streaming: Streaming; podId: string; conversationId: string | null }) {
    return (
        <Reply lemma={lemma}>
            {streaming.text ? <Speech text={streaming.text} streaming /> : null}
            <RunLine notes={liveNote(streaming)} live writing={Boolean(streaming.text)} span="" name={lemma.name} podId={podId} conversationId={conversationId} />
        </Reply>
    );
}
