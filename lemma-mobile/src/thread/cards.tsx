import { useState, type ReactNode } from "react";
import { Platform, ScrollView, View } from "react-native";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { connectLinkLabel } from "@web/data/pod-connectors";
import { adminRoutes } from "../areas/admin/routes";
import { moreRoutes } from "../areas/more/routes";
import { useShownFile } from "../areas/chat/hooks";
import { routes } from "../data/routes";
import { tintOf } from "../ui/face";
import { Icon } from "../ui/icons";
import { Button, Press } from "../ui/press";
import { Num, Text } from "../ui/text";
import { color } from "../ui/tokens";
import { Glyph, type GlyphName } from "./glyphs";
import { clockOf } from "@web/thread/turns";
import {
    askCardView,
    restLength,
    waitEnding,
    type BrowserStep,
    type ConnectorRun,
    type FileChange,
    type FileRead,
    type FileSearch,
    type ImageLook,
    type SignInAsk,
    type SourceList,
    type SubTask,
    type TeammateAsk,
    type TerminalRun,
    type ToolCard,
    type WaitFor,
} from "@web/thread/tool-cards";

/** The tools a chat reads instead of summarising, inside the fold of a
 *  turn's work: the web's `tool-card-view.tsx`, for a phone.
 *
 *  One shape for all of them, as there: a line with a glyph, what happened
 *  and a status at its end, and what came back under it once opened. Closed
 *  by default wherever the content can be long; a run that greps forty
 *  times is normal, and forty open terminal blocks is not a chat any more.
 *  Status is coloured only where a reader scans for it: amber for a failure,
 *  violet for something still going or waiting on them. */

type Tone = "bad" | "wait" | "ok" | undefined;

const MONO = Platform.select({ ios: "Menlo", default: "monospace" });

function toneOf(tone: Tone): string {
    return tone === "bad" ? color.amber : tone === "wait" ? color.violetInk : color.ink3;
}

/** The site's own icon at `/favicon.ico`, else its first letter on a tint
 *  hashed from the host: something stable to know a site by at a glance. */
export function SiteMark({ host, size = 16 }: { host: string; size?: number }) {
    const [broke, setBroke] = useState(false);
    const real = /^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(:\d+)?$/i.test(host) && !broke;
    return (
        <View style={{ width: size, height: size, borderRadius: size * 0.28, overflow: "hidden", backgroundColor: tintOf(host || "?"), alignItems: "center", justifyContent: "center" }}>
            {real ? (
                <Image source={{ uri: "https://" + host + "/favicon.ico" }} style={{ width: size, height: size }} onError={() => setBroke(true)} />
            ) : (
                <Text size={size * 0.62} tone={color.ink}>{(host.replace(/^www\./, "")[0] ?? "?").toUpperCase()}</Text>
            )}
        </View>
    );
}

function Head({ icon, what, meta, status, tone, open, onToggle, label }: {
    icon: ReactNode;
    what: ReactNode;
    meta?: string;
    status?: string;
    tone?: Tone;
    open?: boolean;
    onToggle?: () => void;
    /** What a screen reader says for the line. */
    label: string;
}) {
    const inside = (
        <>
            <View style={{ width: 18, alignItems: "center", paddingTop: 1 }}>{icon}</View>
            <View style={{ flex: 1, minWidth: 0, flexDirection: "row", alignItems: "baseline", gap: 6 }}>
                <View style={{ flexShrink: 1, minWidth: 0 }}>{typeof what === "string" ? <Text size={13.5} numberOfLines={1}>{what}</Text> : what}</View>
                {meta ? <Text size={12} tone={color.ink3} numberOfLines={1} style={{ flexShrink: 2 }}>{meta}</Text> : null}
            </View>
            {status ? <Text size={12} tone={toneOf(tone)} numberOfLines={1}>{status}</Text> : null}
            {onToggle ? (
                <View style={{ transform: [{ rotate: open ? "90deg" : "0deg" }] }}>
                    <Icon name="forward" size={12} width={2} tone={color.ink3} />
                </View>
            ) : null}
        </>
    );
    const row = { flexDirection: "row" as const, alignItems: "center" as const, gap: 8, minHeight: 32, paddingVertical: 4 };
    if (!onToggle) return <View accessibilityLabel={label} style={row}>{inside}</View>;
    return (
        <Press label={label + (open ? ". Hide" : ". Show")} accessibilityState={{ expanded: Boolean(open) }} onPress={onToggle} style={row}>
            {inside}
        </Press>
    );
}

function Body({ children }: { children: ReactNode }) {
    return <View style={{ gap: 6, paddingLeft: 26, paddingBottom: 6 }}>{children}</View>;
}

function Note({ children, tone }: { children: ReactNode; tone?: Tone }) {
    return <Text size={13} tone={tone === "bad" ? color.amber : color.ink2} style={{ lineHeight: 18.5 }} selectable>{children}</Text>;
}

function Fine({ children }: { children: ReactNode }) {
    return <Text size={12.5} tone={color.ink3} style={{ lineHeight: 17 }}>{children}</Text>;
}

/** What a command printed, or a file's lines: monospaced, scrolled sideways
 *  rather than wrapped, and held to a height. */
function Out({ text, tone }: { text: string; tone?: "err" | "value" }) {
    return (
        <View style={{ backgroundColor: color.chrome, borderRadius: 10, maxHeight: 260, overflow: "hidden" }}>
            <ScrollView nestedScrollEnabled>
                <ScrollView horizontal nestedScrollEnabled contentContainerStyle={{ padding: 10 }}>
                    <Text selectable style={{ fontFamily: MONO, fontSize: 12, lineHeight: 17, color: tone === "err" ? color.amber : color.ink }}>{text}</Text>
                </ScrollView>
            </ScrollView>
        </View>
    );
}

/** A path or a value with no bound on its length: one line, the whole of it
 *  on a tap. */
function Clipped({ text }: { text: string }) {
    const [open, setOpen] = useState(false);
    if (!text) return null;
    return (
        <Press label={open ? text : `${text}. Show all of it`} onPress={() => setOpen((was) => !was)}>
            <Text size={12.5} tone={color.ink3} numberOfLines={open ? undefined : 1} style={{ fontFamily: MONO }}>{text}</Text>
        </Press>
    );
}

function Code({ children }: { children: string }) {
    return <Text size={13.5} numberOfLines={1} style={{ fontFamily: MONO, fontSize: 12.5 }}>{children}</Text>;
}

function useToggle(): [boolean, () => void] {
    const [open, setOpen] = useState(false);
    return [open, () => setOpen((was) => !was)];
}

/* ── the browser ───────────────────────────────────────────────────────── */

const BROWSER_GLYPH: Record<BrowserStep["did"], GlyphName> = { open: "globe", act: "pointer", read: "text", snapshot: "tree", shot: "image" };

function browserLine(step: BrowserStep): string {
    switch (step.did) {
        case "open":
            return [step.host || step.what, step.trail].filter(Boolean).join(" ");
        case "read":
            return "Read " + step.what;
        case "snapshot":
            return "Mapped " + step.what;
        case "shot":
            return "Screenshot of " + (step.host || "the page");
        default:
            return step.what;
    }
}

function browserMeta(step: BrowserStep): string | undefined {
    if (step.did === "open") return step.title || undefined;
    if (step.did === "act") return step.host || undefined;
    if (step.did === "shot") return [step.what, step.size].filter(Boolean).join(" · ") || undefined;
    return step.size || undefined;
}

function BrowserCard({ step }: { step: BrowserStep }) {
    const [open, toggle] = useToggle();
    const status = step.pending ? "running" : step.failed ? "failed" : step.truncated ? "cut short" : "";
    const tone: Tone = step.failed ? "bad" : step.pending ? "wait" : undefined;
    const more = Boolean(step.comment || step.error || step.body || step.asked || step.seen || step.awaited || step.title) || step.did === "shot" || (step.did !== "open" && Boolean(step.url));
    return (
        <View>
            <Head
                icon={step.did === "open" && step.host ? <SiteMark host={step.host} size={15} /> : <Glyph name={BROWSER_GLYPH[step.did]} size={15} tone={color.ink2} />}
                what={browserLine(step)}
                meta={browserMeta(step)}
                status={status}
                tone={tone}
                open={more ? open : undefined}
                onToggle={more ? toggle : undefined}
                label={browserLine(step)}
            />
            {more && open ? (
                <Body>
                    {step.comment ? <Note>{step.comment}</Note> : null}
                    {step.error ? <Note tone="bad">{step.error}</Note> : null}
                    {step.url ? <Clipped text={step.url} /> : null}
                    {step.title && step.did !== "open" ? <Note>{step.title}</Note> : null}
                    {step.awaited ? <Fine>Waited for {step.awaited}</Fine> : null}
                    {step.moved ? <Note>Asked for {step.what}, and ended up here.</Note> : null}
                    {step.did === "shot" ? (
                        <>
                            {step.asked ? <Note>Looking for: {step.asked}</Note> : null}
                            {step.seen ? <Note>{step.seen}</Note> : !step.failed && !step.pending ? (
                                <Fine>Captured {step.fullPage ? "the whole page" : "the screen"}{step.size ? ", " + step.size : ""}. The picture is not kept in the chat.</Fine>
                            ) : null}
                        </>
                    ) : null}
                    {step.body ? <Out text={step.body} /> : null}
                    {step.truncated ? <Fine>Cut at the size this step asked for.</Fine> : null}
                    {step.pending ? <Note>Nothing has come back yet.</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── a picture it stopped to look at ──────────────────────────────────── */

function PodImage({ podId, path, name }: { podId: string; path: string; name: string }) {
    const file = useShownFile(podId, path);
    const [broke, setBroke] = useState(false);
    if (file.isPending) return <Note>Fetching it…</Note>;
    const url = file.data?.url;
    if (file.isError || !url || broke) return <Fine>That picture could not be fetched. It may have moved, or you may not have access to it.</Fine>;
    return <Image accessibilityLabel={name} source={{ uri: url }} contentFit="contain" onError={() => setBroke(true)} style={{ width: "100%", aspectRatio: 4 / 3, borderRadius: 10, backgroundColor: color.chrome }} />;
}

const STORE_WORD: Record<ImageLook["store"], string> = { pod: "in files", workspace: "on the computer", "": "" };

function ImageCard({ look, podId }: { look: ImageLook; podId: string }) {
    const [open, toggle] = useToggle();
    const [shown, setShown] = useState(false);
    const fetchable = look.store === "pod" && !look.failed;
    return (
        <View>
            <Head icon={<Glyph name="image" size={15} tone={color.ink2} />} what={look.name} meta={look.weight || undefined} status={look.pending ? "running" : look.failed ? "failed" : ""} tone={look.failed ? "bad" : look.pending ? "wait" : undefined} open={open} onToggle={toggle} label={"Looked at " + look.name} />
            {open ? (
                <Body>
                    {look.asked ? <Note>Looking for: {look.asked}</Note> : null}
                    {look.error ? <Note tone="bad">{look.error}</Note> : null}
                    <Clipped text={[STORE_WORD[look.store], look.path].filter(Boolean).join(" · ")} />
                    {look.described ? <Note>{look.described}</Note> : null}
                    {look.pending && !shown ? <Note>Nothing has come back yet.</Note> : null}
                    {shown ? <PodImage podId={podId} path={look.path} name={look.name} /> : fetchable ? (
                        <Press label="Show the picture" onPress={() => setShown(true)} style={{ alignSelf: "flex-start", minHeight: 32, justifyContent: "center" }}>
                            <Text size={13.5} tone={color.violetInk}>Show the picture</Text>
                        </Press>
                    ) : look.store === "workspace" ? <Fine>It is on the Lemma’s computer, which the phone does not open.</Fine> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── a command, and what it printed ───────────────────────────────────── */

function terminalStatus(run: TerminalRun): { text: string; tone?: Tone } {
    if (run.pending) return { text: "running", tone: "wait" };
    if (run.running) return { text: "still running", tone: "wait" };
    if (run.exitCode !== undefined) return { text: "exit " + run.exitCode, tone: run.exitCode === 0 ? undefined : "bad" };
    if (run.failed) return { text: "failed", tone: "bad" };
    return { text: "done" };
}

function TerminalCard({ run }: { run: TerminalRun }) {
    const [open, toggle] = useToggle();
    const status = terminalStatus(run);
    const nothing = !run.output && !run.errorOutput && !run.value;
    const first = run.command.split("\n")[0];
    return (
        <View>
            <Head icon={<Glyph name={run.language === "python" ? "code" : "terminal"} size={15} tone={color.ink2} />} what={<Code>{first}</Code>} meta={run.lines ? run.lines + (run.lines === 1 ? " line" : " lines") : undefined} status={status.text} tone={status.tone} open={open} onToggle={toggle} label={"Ran " + first} />
            {open ? (
                <Body>
                    {run.comment ? <Note>{run.comment}</Note> : null}
                    {run.command.includes("\n") ? <Out text={run.command} /> : null}
                    {run.workdir ? <Clipped text={"in " + run.workdir} /> : null}
                    {run.output ? <Out text={run.output} /> : null}
                    {run.errorOutput ? <Out text={run.errorOutput} tone="err" /> : null}
                    {run.value ? <Out text={run.value} tone="value" /> : null}
                    {nothing && !run.pending ? <Note>It printed nothing.</Note> : null}
                    {run.pending ? <Note>Nothing has come back yet.</Note> : null}
                    {run.running ? <Note>Still running{run.processId ? " as " + run.processId : ""}. Nothing was cancelled.</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── where an answer came from ────────────────────────────────────────── */

const SOURCES_SHOWN = 4;

function SourcesCard({ list }: { list: SourceList }) {
    const [all, setAll] = useState(false);
    const shown = all ? list.sources : list.sources.slice(0, SOURCES_SHOWN);
    const rest = list.sources.length - shown.length;
    const count = list.sources.length;
    const what = list.action === "search" ? "Searched the web for “" + list.query + "”" : count === 1 ? "Read one page" : "Read " + count + " pages";
    const status = list.error ? "failed" : list.pending ? "running" : list.action === "search" && list.listed ? count + (count === 1 ? " result" : " results") : "";
    const body = count > 0 || (!list.pending && (list.listed || list.text)) || list.error || list.note || list.asked;
    return (
        <View>
            <Head icon={<Glyph name="globe" size={15} tone={color.ink2} />} what={what} status={status} tone={list.error ? "bad" : list.pending ? "wait" : undefined} label={what} />
            {body ? (
                <Body>
                    {list.error ? <Note tone="bad">{list.error}</Note> : null}
                    {list.note ? <Note>{list.note}</Note> : null}
                    {list.asked ? <Note>Looking for: {list.asked}</Note> : null}
                    {!list.error && list.listed && count === 0 && !list.pending && !list.text ? <Note>Nothing came back.</Note> : null}
                    {shown.map((source) => (
                        <Press
                            key={source.url}
                            role="link"
                            label={`${source.title}, ${source.host}. Open`}
                            onPress={() => void WebBrowser.openBrowserAsync(source.url).catch(() => undefined)}
                            style={{ flexDirection: "row", gap: 8, paddingVertical: 4, opacity: source.failed ? 0.6 : 1 }}
                        >
                            <View style={{ paddingTop: 2 }}><SiteMark host={source.host} size={16} /></View>
                            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                                <Text size={13.5} tone={color.violetInk} numberOfLines={2}>{source.title}</Text>
                                <Text size={12} tone={color.ink3} numberOfLines={1}>
                                    {[source.host, source.publisher !== source.host ? source.publisher : "", source.published].filter(Boolean).join(" · ")}
                                </Text>
                                {source.snippet ? <Text size={12.5} tone={color.ink2} numberOfLines={3} style={{ lineHeight: 17 }}>{source.snippet}</Text> : null}
                                {source.savedAs ? <Text size={12} tone={color.ink3} numberOfLines={1} style={{ fontFamily: MONO }}>{source.savedAs}</Text> : null}
                                {source.error ? <Text size={12} tone={color.amber}>{source.error}</Text> : null}
                            </View>
                            <Glyph name="external" size={12} tone={color.ink3} />
                        </Press>
                    ))}
                    {rest > 0 ? (
                        <Press label={`${rest} more`} onPress={() => setAll(true)} style={{ alignSelf: "flex-start", minHeight: 30, justifyContent: "center" }}>
                            <Text size={13} tone={color.violetInk}>{rest} more</Text>
                        </Press>
                    ) : null}
                    {list.text ? <Out text={list.text} /> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── a local agent's files ────────────────────────────────────────────── */

function outcome(card: { pending: boolean; failed: boolean }): { text: string; tone?: Tone } {
    if (card.pending) return { text: "running", tone: "wait" };
    if (card.failed) return { text: "failed", tone: "bad" };
    return { text: "" };
}

function ReadCard({ read }: { read: FileRead }) {
    const [open, toggle] = useToggle();
    const status = outcome(read);
    const meta = [read.range, read.lines ? read.lines + (read.lines === 1 ? " line" : " lines") : ""].filter(Boolean).join(" · ");
    return (
        <View>
            <Head icon={<Glyph name="text" size={15} tone={color.ink2} />} what={<Text size={13.5} numberOfLines={1}>Read <Text style={{ fontFamily: MONO, fontSize: 12.5 }}>{read.name}</Text></Text>} meta={meta || undefined} status={status.text} tone={status.tone} open={open} onToggle={toggle} label={"Read " + read.name} />
            {open ? (
                <Body>
                    <Clipped text={read.path} />
                    {read.error ? <Note tone="bad">{read.error}</Note> : null}
                    {read.content ? <Out text={read.content} /> : null}
                    {!read.content && !read.pending && !read.failed ? <Note>It was empty.</Note> : null}
                    {read.pending ? <Note>Nothing has come back yet.</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

const CHANGE_VERB: Record<FileChange["action"], string> = { write: "Wrote", edit: "Edited", delete: "Deleted", move: "Moved" };
const CHANGE_GLYPH: Record<FileChange["action"], GlyphName> = { write: "edit", edit: "edit", delete: "trash", move: "move" };

function tally(added: number, removed: number): string {
    return [added ? "+" + added : "", removed ? "−" + removed : ""].filter(Boolean).join(" ");
}

function ChangeCard({ change }: { change: FileChange }) {
    const [open, toggle] = useToggle();
    const status = outcome(change);
    const more = change.files.length > 0 || Boolean(change.error || change.message || change.destination);
    const said = CHANGE_VERB[change.action] + " " + change.name + (change.destination ? " to " + change.destination : "");
    return (
        <View>
            <Head
                icon={<Glyph name={CHANGE_GLYPH[change.action]} size={15} tone={color.ink2} />}
                what={<Text size={13.5} numberOfLines={1}>{CHANGE_VERB[change.action]} <Text style={{ fontFamily: MONO, fontSize: 12.5 }}>{change.name}</Text>{change.destination ? <> to <Text style={{ fontFamily: MONO, fontSize: 12.5 }}>{change.destination}</Text></> : null}</Text>}
                meta={tally(change.added, change.removed) || undefined}
                status={status.text}
                tone={status.tone}
                open={more ? open : undefined}
                onToggle={more ? toggle : undefined}
                label={said}
            />
            {more && open ? (
                <Body>
                    {change.error ? <Note tone="bad">{change.error}</Note> : null}
                    {change.destination ? <Clipped text={change.path + " → " + change.destination} /> : null}
                    {change.files.map((file) => (
                        <View key={file.path} style={{ gap: 4 }}>
                            <Clipped text={file.path + (file.change === "add" ? " · new" : file.change === "delete" ? " · removed" : "")} />
                            {file.lines.length > 0 ? (
                                <View style={{ backgroundColor: color.chrome, borderRadius: 10, maxHeight: 260, overflow: "hidden" }}>
                                    <ScrollView nestedScrollEnabled>
                                        <ScrollView horizontal nestedScrollEnabled contentContainerStyle={{ padding: 10 }}>
                                            <View>
                                                {file.lines.map((line, index) => (
                                                    <Text key={index} selectable style={{ fontFamily: MONO, fontSize: 12, lineHeight: 17, color: line.sign === "+" ? color.ink : line.sign === "-" ? color.amber : color.ink3, backgroundColor: line.sign === "+" ? color.faint : "transparent" }}>
                                                        {line.sign === "gap" ? "⋯" : line.sign + " " + line.text}
                                                    </Text>
                                                ))}
                                            </View>
                                        </ScrollView>
                                    </ScrollView>
                                </View>
                            ) : null}
                        </View>
                    ))}
                    {change.message && !change.error ? <Fine>{change.message}</Fine> : null}
                    {change.pending ? <Note>Nothing has come back yet.</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

function searchLine(search: FileSearch): string {
    if (search.action === "list") return search.path ? "Listed " + search.path : search.title || "Listed files";
    if (!search.pattern) return search.title || (search.action === "grep" ? "Searched the files" : "Matched files");
    return (search.action === "grep" ? "Searched for " : "Matched ") + search.pattern;
}

function SearchCard({ search }: { search: FileSearch }) {
    const [open, toggle] = useToggle();
    const status = outcome(search);
    const unit = search.action === "grep" ? " line" : " file";
    return (
        <View>
            <Head icon={<Glyph name={search.action === "list" ? "folder" : "text"} size={15} tone={color.ink2} />} what={searchLine(search)} meta={search.pending || search.failed ? undefined : search.count + unit + (search.count === 1 ? "" : "s")} status={status.text} tone={status.tone} open={open} onToggle={toggle} label={searchLine(search)} />
            {open ? (
                <Body>
                    {(search.path || search.filter) && search.action !== "list" ? <Fine>{["in " + (search.path || "the working folder"), search.filter ? "files matching " + search.filter : ""].filter(Boolean).join(", ")}</Fine> : null}
                    {search.error ? <Note tone="bad">{search.error}</Note> : null}
                    {search.output ? <Out text={search.output} /> : null}
                    {!search.output && !search.pending && !search.failed ? <Note>Nothing matched.</Note> : null}
                    {search.pending ? <Note>Nothing has come back yet.</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── a sub-agent, and another Lemma ───────────────────────────────────── */

function TaskCard({ task }: { task: SubTask }) {
    const [open, toggle] = useToggle();
    const status = task.pending ? { text: "working", tone: "wait" as const } : outcome(task);
    return (
        <View>
            <Head icon={<Glyph name="agent" size={15} tone={color.ink2} />} what={task.description} meta={task.agentType || undefined} status={status.text} tone={status.tone} open={open} onToggle={toggle} label={task.description} />
            {open ? (
                <Body>
                    {task.error ? <Note tone="bad">{task.error}</Note> : null}
                    {task.prompt && task.prompt !== task.description ? <Note>{task.prompt}</Note> : null}
                    {task.output ? <Out text={task.output} tone="value" /> : null}
                    {task.pending ? <Note>Still working.</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

/** A request put to another Lemma: what was asked and what came back, and,
 *  when it was asked as you, the way into that Lemma's side of it. */
function AskCard({ ask }: { ask: TeammateAsk }) {
    const router = useRouter();
    const [open, toggle] = useToggle();
    const view = askCardView(ask);
    return (
        <View>
            <Head icon={<Glyph name="agent" size={15} tone={color.ink2} />} what={"Asked " + ask.teammate} status={view.status} tone={view.tone as Tone} open={open} onToggle={toggle} label={"Asked " + ask.teammate} />
            {open ? (
                <Body>
                    <Note>{ask.request}</Note>
                    {ask.error ? <Note tone="bad">{ask.error}</Note> : null}
                    {ask.answer ? <Out text={ask.answer} tone="value" /> : null}
                    {view.line ? <Note>{view.line}</Note> : null}
                    {view.linkable ? (
                        <Press role="link" label={`Open ${ask.teammate}’s chat`} onPress={() => router.push(routes.chat(ask.podId, ask.conversationId) as never)} style={{ alignSelf: "flex-start", minHeight: 30, justifyContent: "center" }}>
                            <Text size={13} tone={color.violetInk}>Open {ask.teammate}’s chat</Text>
                        </Press>
                    ) : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── one call out to a connected account ──────────────────────────────── */

function ConnectorCard({ run }: { run: ConnectorRun }) {
    const router = useRouter();
    const [open, toggle] = useToggle();
    return (
        <View>
            <Head icon={<Glyph name="plug" size={15} tone={color.ink2} />} what={run.connector} meta={run.operation} status={run.pending ? "running" : run.failed ? "failed" : "done"} tone={run.failed ? "bad" : run.pending ? "wait" : undefined} open={open} onToggle={toggle} label={run.connector + ", " + run.operation} />
            {open ? (
                <Body>
                    <Fine>{run.operation}</Fine>
                    {run.error ? <Note tone={run.failed ? "bad" : undefined}>{run.error}</Note> : null}
                    {/* The connector said where its account is connected:
                        that link is the fix, and it is the reader's own. */}
                    {run.failed && run.connectUrl ? (
                        <Press role="link" label={connectLinkLabel(run.code) + " for " + run.connector} onPress={() => void WebBrowser.openBrowserAsync(run.connectUrl)} style={{ alignSelf: "flex-start", minHeight: 30, justifyContent: "center" }}>
                            <Text size={13} tone={color.violetInk}>{connectLinkLabel(run.code)}</Text>
                        </Press>
                    ) : null}
                    {run.failed && !run.connectUrl ? (
                        /* Otherwise a failed call is most often an account
                           that needs connecting again. */
                        <Press role="link" label={`Check ${run.connector} in Connectors`} onPress={() => router.push(adminRoutes.section("connectors") as never)} style={{ alignSelf: "flex-start", minHeight: 30, justifyContent: "center" }}>
                            <Text size={13} tone={color.violetInk}>Check {run.connector} in Connectors</Text>
                        </Press>
                    ) : null}
                    {run.params.map((param) => (
                        <View key={param.name} style={{ flexDirection: "row", gap: 8 }}>
                            <Text size={12.5} tone={color.ink3} style={{ width: 84 }} numberOfLines={1}>{param.name}</Text>
                            <View style={{ flex: 1, minWidth: 0 }}><Clipped text={param.value} /></View>
                        </View>
                    ))}
                    {run.summary ? <Note>Came back with {run.summary}.</Note> : null}
                    {run.savedTo ? <Clipped text={"saved to " + run.savedTo} /> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── a run that is deliberately waiting ───────────────────────────────── */

function waitingStatus(wait: WaitFor): string {
    if (wait.on === "process") return "until the command finishes";
    if (wait.on === "subagent") return "until the sub-agent finishes";
    if (wait.wakeAtMs !== undefined) return "back at " + clockOf(new Date(wait.wakeAtMs).toISOString());
    return wait.seconds === undefined ? "waiting" : "back in " + restLength(wait.seconds);
}

function WaitCard({ wait }: { wait: WaitFor }) {
    const status = wait.waiting ? waitingStatus(wait) : waitEnding(wait);
    const meta = wait.waiting && wait.on === "time" && wait.seconds !== undefined ? "waiting " + restLength(wait.seconds) : undefined;
    const waited = wait.waitedSeconds === undefined ? "" : restLength(wait.waitedSeconds);
    return (
        <View>
            <Head icon={<Glyph name="wait" size={15} tone={color.ink2} />} what={wait.reason} meta={meta} status={status} tone={wait.error ? "bad" : wait.waiting ? "wait" : undefined} label={wait.reason + ", " + status} />
            {wait.error || waited || wait.note ? (
                <Body>
                    {wait.error ? <Note tone="bad">{wait.error}</Note> : null}
                    {waited ? <Fine>Waited {waited}</Fine> : null}
                    {wait.note ? <Note>Next: {wait.note}</Note> : null}
                </Body>
            ) : null}
        </View>
    );
}

/* ── a login wall, which only a person can get past ───────────────────── */

function signInStatus(ask: SignInAsk): { text: string; tone?: Tone } {
    if (!ask.resolved) return { text: "needs you", tone: "wait" };
    if (ask.signedIn) return { text: ask.kept ? "kept for next time" : "signed in" };
    if (ask.outcome === "declined") return { text: "skipped" };
    if (ask.outcome === "expired") return { text: "nobody answered" };
    return { text: "could not ask", tone: "bad" };
}


export function SignInCard({ ask, podId, conversationId, toolCallId, docked }: {
    ask: SignInAsk;
    podId: string;
    conversationId?: string | null;
    toolCallId?: string;
    /** On the shelf above the composer, as one line: the site, why, and the
     *  button. */
    docked?: boolean;
}) {
    const router = useRouter();
    const status = signInStatus(ask);
    const answerable = Boolean(conversationId && toolCallId);
    const open = () => {
        if (!conversationId || !toolCallId) return;
        /* The phone's own sign-in screen (area G), at the web page's path:
           the Lemma's browser, drivable, with the answers that let the run
           carry on. */
        router.push(moreRoutes.signIn(conversationId, toolCallId) as never);
    };
    if (docked) {
        return (
            <View accessibilityLabel={"Waiting on you to sign in to " + ask.host} style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 10, paddingHorizontal: 12 }}>
                <SiteMark host={ask.host} size={22} />
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text size={14.5} weight="medium" numberOfLines={1}>Sign in to {ask.host}</Text>
                    {ask.reason ? <Text size={12.5} tone={color.ink2} numberOfLines={1}>{ask.reason}</Text> : null}
                </View>
                {answerable ? <Button title="Sign in" height={36} size={14} onPress={open} /> : <Text size={12.5} tone={color.ink3}>Opens on the web</Text>}
            </View>
        );
    }
    return (
        <View style={{ alignSelf: "stretch", borderTopWidth: 1, borderBottomWidth: 1, borderColor: color.line, paddingVertical: 6 }}>
            <Head icon={<SiteMark host={ask.host} size={15} />} what={ask.resolved ? (ask.signedIn ? "Signed in to " : "Not signed in to ") + ask.host : "Sign in to " + ask.host} status={status.text} tone={status.tone} label={"Sign in to " + ask.host + ", " + status.text} />
            {ask.reason || !ask.resolved ? (
                <Body>
                    {ask.reason ? <Note>{ask.reason}</Note> : null}
                    {!ask.resolved ? (answerable ? (
                        <>
                            <Fine>It opens the Lemma’s browser on {ask.host}, where you sign in.</Fine>
                            <View style={{ alignSelf: "flex-start" }}><Button title={"Sign in to " + ask.host} height={36} size={14} onPress={open} /></View>
                        </>
                    ) : <Fine>This sign-in cannot be opened from here.</Fine>) : null}
                </Body>
            ) : null}
        </View>
    );
}

export function ToolCardView({ card, podId, conversationId, toolCallId }: { card: ToolCard; podId: string; conversationId?: string | null; toolCallId?: string }) {
    switch (card.kind) {
        case "sign-in":
            return <SignInCard ask={card} podId={podId} conversationId={conversationId} toolCallId={toolCallId} />;
        case "browser":
            return <BrowserCard step={card} />;
        case "terminal":
            return <TerminalCard run={card} />;
        case "sources":
            return <SourcesCard list={card} />;
        case "connector":
            return <ConnectorCard run={card} />;
        case "wait":
            return <WaitCard wait={card} />;
        case "image":
            return <ImageCard look={card} podId={podId} />;
        case "file-read":
            return <ReadCard read={card} />;
        case "file-change":
            return <ChangeCard change={card} />;
        case "file-search":
            return <SearchCard search={card} />;
        case "task":
            return <TaskCard task={card} />;
        case "ask":
            return <AskCard ask={card} />;
    }
}

/** A time on a card, set as a number. */
export function At({ at }: { at: string }) {
    return at ? <Num size={12} tone={color.ink3}>{at}</Num> : null;
}
