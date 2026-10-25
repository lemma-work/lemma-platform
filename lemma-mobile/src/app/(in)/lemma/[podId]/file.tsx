import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, TextInput, View } from "react-native";
import { useLocalSearchParams, useNavigation, useRouter } from "expo-router";
import { SafeAreaView } from "react-native-safe-area-context";
import { useLemma } from "../../../../data/hooks";
import { routes } from "../../../../data/routes";
import { CommentsSheet } from "../../../../areas/files/comments";
import { PageEditor, type PageEditorHandle } from "../../../../areas/files/editor";
import { FilesIcon } from "../../../../areas/files/glyphs";
import { useComments, useCommentsStatus, useFileContent, useFilesSource, useWrites } from "../../../../areas/files/hooks";
import { isPersonal, nameOf, parentOf, PDF_LIMIT } from "../../../../areas/files/model";
import { ConfirmDelete, ItemSheet, MoveSheet, NameSheet, WriteProblem } from "../../../../areas/files/parts";
import { filesRoutes } from "../../../../areas/files/routes";
import { keepCopy, openElsewhere } from "../../../../areas/files/save";
import { ShareSheet } from "../../../../areas/more/share/sheet";
import type { LibraryItem } from "../../../../areas/files/source";
import { tablesRoutes } from "../../../../areas/tables/routes";
import { AudioPlayer, VideoPlayer, ZoomImage } from "../../../../areas/files/viewers/media";
import { HtmlView } from "../../../../areas/files/viewers/html";
import { PdfView } from "../../../../areas/files/viewers/pdf";
import { OtherFile, ReadPage, TextView } from "../../../../areas/files/viewers/text";
import { threadsOf } from "@web/docpages/comments/model";
import { docTitle } from "@web/library/doc-title";
import { describeSave, lockedBecause } from "@web/thread/document-save";
import type { Anchor } from "../../../../areas/files/web/protocol";
import { BackLink, Problem } from "../../../../ui/chrome";
import { Icon } from "../../../../ui/icons";
import { Button, Press } from "../../../../ui/press";
import { Sheet } from "../../../../ui/sheet";
import { Num, Text } from "../../../../ui/text";
import { color, font, TOUCH } from "../../../../ui/tokens";

/* The editor's own words for work not yet on the server. */
const UNSAVED = describeSave({ state: "idle", dirty: true })?.label;
const SAVING = describeSave({ state: "saving", dirty: true })?.label;

/** A file of a Lemma's, whole screen, shown as what it is: a page you write
 *  in, text, a picture you can zoom, a video, a recording, a PDF, a web page,
 *  or, for what nothing here draws, the way to another app. `?path=` names
 *  it; a rename or a move here follows it. */
export default function FileScreen() {
    const router = useRouter();
    const navigation = useNavigation();
    const params = useLocalSearchParams<{ podId: string; path?: string }>();
    const podId = String(params.podId);
    const [path, setPath] = useState(String(params.path ?? ""));
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "the Lemma";
    const source = useFilesSource();
    const file = useFileContent(podId, path);
    const writes = useWrites(podId, parentOf(path));
    const editor = useRef<PageEditorHandle>(null);

    const [status, setStatus] = useState<{ label: string | null; bad: boolean }>({ label: null, bad: false });
    const [typing, setTyping] = useState(false);
    const [menu, setMenu] = useState(false);
    const [sharing, setSharing] = useState(false);
    const [renaming, setRenaming] = useState(false);
    const [moving, setMoving] = useState(false);
    const [deleting, setDeleting] = useState(false);
    const [asking, setAsking] = useState(false);
    const [asked, setAsked] = useState<{ state: "sending" | "sent" | "failed"; text: string; id?: string } | null>(null);
    const [handing, setHanding] = useState<"open" | "keep" | null>(null);
    /* What handing the file on did: a copy kept (said quietly), or why not. */
    const [note, setNote] = useState<{ text: string; ok: boolean } | null>(null);
    const [mediaFailed, setMediaFailed] = useState(false);

    const data = file.data;
    const page = data?.kind === "markdown";
    const personal = isPersonal(path);
    const locked = page && data?.text !== undefined ? lockedBecause(data.text) : null;
    const editing = page && !locked && data?.text !== undefined;

    /* Comments: one table everyone in the Lemma reads, so a personal page has none. */
    const [commentsOpen, setCommentsOpen] = useState(false);
    const [draft, setDraft] = useState<Anchor | "page" | null>(null);
    const [active, setActive] = useState<string | null>(null);
    const commentsStatus = useCommentsStatus(podId);
    const commentable = page && !personal;
    const comments = useComments(podId, path, commentable && commentsStatus.data === "ready");
    const threads = useMemo(() => threadsOf(comments.data ?? []), [comments.data]);
    const openThreads = useMemo(() => threads.filter((one) => !one.root.resolved), [threads]);
    const anchors = useMemo(() => ({
        anchors: openThreads.filter((one) => one.root.quote).map((one) => ({ id: one.root.id, quote: one.root.quote, quotePrefix: one.root.quotePrefix, quoteSuffix: one.root.quoteSuffix })),
        active,
        enabled: Boolean(commentable),
    }), [openThreads, active, commentable]);

    const item: LibraryItem = { id: path, name: data?.name ?? nameOf(path), kind: "file", path, updated: "", detail: data?.mime ?? "", mime: data?.mime, size: data?.size };
    const title = docTitle(item.name);

    /* Leaving a page hands over what is typed first, however the screen is
       left: the back link, the system's back, a swipe. Only when there is
       something to hand over: words not yet written, or a caret still in the
       page, whose title may yet rename it. */
    const leaving = useRef(false);
    const pending = useRef(false);
    pending.current = typing || status.label === UNSAVED || status.label === SAVING;
    useEffect(() => navigation.addListener("beforeRemove", (event) => {
        if (!editing || leaving.current || !pending.current) return;
        event.preventDefault();
        leaving.current = true;
        void editor.current?.flush().finally(() => navigation.dispatch(event.data.action));
    }), [navigation, editing]);

    const back = () => (router.canGoBack() ? router.back() : router.replace(filesRoutes.folder(podId, parentOf(path)) as never));

    const moved = useCallback((next: string) => {
        setPath(next);
        router.setParams({ path: next });
    }, [router]);

    async function handOn(how: "keep" | "open") {
        if (!data) return;
        setHanding(how);
        setNote(null);
        try {
            const uri = await source.toPhone(podId, path);
            if (how === "keep") {
                const done = await keepCopy(uri, data.name, data.mime);
                if (done) setNote({ text: done, ok: true });
            } else {
                await openElsewhere(uri, data.mime);
            }
        } catch (failure) {
            setNote({ text: failure instanceof Error ? failure.message : `${data.name} could not be opened.`, ok: false });
        } finally {
            setHanding(null);
        }
    }

    async function ask(text: string) {
        setAsked({ state: "sending", text });
        try {
            const id = await source.askAboutPage(podId, path, text);
            setAsked({ state: "sent", text, id });
        } catch (failure) {
            setAsked({ state: "failed", text: failure instanceof Error ? failure.message : "That was not sent." });
        }
    }

    const body = () => {
        if (file.isPending) return <ActivityIndicator color={color.ink3} style={{ marginTop: 40 }} />;
        if (file.isError || !data) {
            const missing = (file.error as { statusCode?: number } | null)?.statusCode === 404;
            return <Problem error={new Error(missing ? "This file is not there any more. It may have moved." : "This file could not be read. It may have moved, or you may not have access.")} onRetry={missing ? undefined : () => void file.refetch()} />;
        }
        const other = <OtherFile file={data} busy={handing} onOpen={() => void handOn("open")} onKeep={() => void handOn("keep")} />;
        switch (data.kind) {
            case "markdown":
                if (editing) {
                    return (
                        <PageEditor
                            ref={editor}
                            podId={podId}
                            path={path}
                            text={data.text ?? ""}
                            lemmaName={lemmaName}
                            personal={personal}
                            comments={anchors}
                            onStatus={(label, bad) => setStatus({ label, bad })}
                            onFocus={setTyping}
                            onOpenFile={(to) => router.push(filesRoutes.file(podId, to) as never)}
                            onOpenTable={(name) => router.push(tablesRoutes.table(podId, name) as never)}
                            onAsk={(text) => void ask(text)}
                            onCommentStart={(anchor) => { setDraft(anchor ?? "page"); setCommentsOpen(true); }}
                            onCommentFocus={(id) => { setActive(id); if (id) setCommentsOpen(true); }}
                            onRenamed={moved}
                        />
                    );
                }
                return <ReadPage text={data.text ?? ""} locked={locked} />;
            case "text":
                return data.text !== undefined ? <TextView text={data.text} /> : other;
            case "html":
                return data.text !== undefined ? <HtmlView html={data.text} name={data.name} /> : other;
            case "image":
                return data.rawUrl && !mediaFailed ? <ZoomImage uri={data.rawUrl} label={data.name} onFail={() => setMediaFailed(true)} /> : other;
            case "video":
                return data.rawUrl && !mediaFailed ? <VideoPlayer uri={data.rawUrl} onFail={() => setMediaFailed(true)} /> : other;
            case "audio":
                return data.rawUrl && !mediaFailed ? <AudioPlayer uri={data.rawUrl} name={title} /> : other;
            case "pdf":
                return data.size > PDF_LIMIT ? <OtherFile file={{ ...data, note: "Too large to show here" }} busy={handing} onOpen={() => void handOn("open")} onKeep={() => void handOn("keep")} /> : <PdfView podId={podId} path={path} name={data.name} />;
            default:
                return other;
        }
    };

    const backLabel = parentOf(path) === "/" ? "Files" : parentOf(path) === "/me" ? "Mine" : parentOf(path) === "/pages" ? "Pages" : nameOf(parentOf(path));

    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined} style={{ flex: 1 }}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingTop: 4, paddingLeft: 6, paddingRight: 6, minHeight: TOUCH + 8, borderBottomWidth: page ? 0 : 1, borderBottomColor: color.line }}>
                    <BackLink label={backLabel} onPress={back} />
                    <View style={{ flex: 1, minWidth: 0, paddingHorizontal: 4 }}>
                        {!page ? <Text size={16} weight="medium" numberOfLines={1}>{title}</Text> : null}
                        {status.label ? (
                            <Press label={status.bad ? `${status.label}. Try again` : status.label} disabled={!status.bad} onPress={() => editor.current?.retry()}>
                                <Text size={12.5} tone={status.bad ? color.amber : color.ink3} numberOfLines={1}>{status.label}{status.bad ? " · Try again" : ""}</Text>
                            </Press>
                        ) : null}
                    </View>
                    {typing ? (
                        <Press label="Done typing" onPress={() => editor.current?.blur()} style={{ height: TOUCH, paddingHorizontal: 12, justifyContent: "center" }}>
                            <Text size={15.5} weight="medium">Done</Text>
                        </Press>
                    ) : (
                        <>
                            {commentable ? (
                                <Press label={openThreads.length ? `Comments, ${openThreads.length} open` : "Comments"} onPress={() => { setDraft(null); setCommentsOpen(true); }} style={{ height: TOUCH, minWidth: TOUCH, paddingHorizontal: 8, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4 }}>
                                    <FilesIcon name="comment" size={20} tone={color.ink} />
                                    {openThreads.length ? <Num size={13} tone={color.ink2}>{openThreads.length}</Num> : null}
                                </Press>
                            ) : null}
                            {data ? (
                                <Press label="Share" onPress={() => setSharing(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                                    <FilesIcon name="share" size={20} tone={color.ink} />
                                </Press>
                            ) : null}
                            <Press label="More" onPress={() => setMenu(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                                <Icon name="more" size={20} />
                            </Press>
                        </>
                    )}
                </View>
                {writes.problem || note ? (
                    <View style={{ paddingHorizontal: 16 }}>
                        <WriteProblem problem={writes.problem ?? (note && !note.ok ? note.text : null)} onDismiss={() => { writes.clearProblem(); setNote(null); }} />
                        {note?.ok ? <Text size={13.5} tone={color.ink2} style={{ paddingVertical: 8 }} accessibilityLiveRegion="polite">{note.text}</Text> : null}
                    </View>
                ) : null}
                <View style={{ flex: 1 }}>{body()}</View>
                {asked ? (
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingVertical: 10, borderTopWidth: 1, borderTopColor: color.line, backgroundColor: asked.state === "failed" ? color.amberWash : color.paper }}>
                        {asked.state === "sending" ? <ActivityIndicator size="small" color={color.violet} /> : asked.state === "sent" ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} /> : null}
                        <Text size={14} tone={asked.state === "failed" ? color.amber : color.ink2} numberOfLines={2} style={{ flex: 1 }}>
                            {asked.state === "sending" ? `Asking ${lemmaName}…` : asked.state === "sent" ? `${lemmaName} is on it. It writes into this page.` : asked.text}
                        </Text>
                        {asked.state === "sent" && asked.id ? (
                            <Press label="Open the chat" onPress={() => router.push(routes.chat(podId, asked.id!) as never)} style={{ height: 36, justifyContent: "center", paddingHorizontal: 4 }}>
                                <Text size={14} tone={color.ink}>Open the chat</Text>
                            </Press>
                        ) : null}
                        <Press label="Dismiss" onPress={() => setAsked(null)} style={{ padding: 4 }}>
                            <Icon name="close" size={16} tone={color.ink3} />
                        </Press>
                    </View>
                ) : null}
            </KeyboardAvoidingView>

            <ItemSheet
                item={menu ? item : null}
                onClose={() => setMenu(false)}
                actions={[
                    { icon: "spark", label: `Ask ${lemmaName} about this`, run: () => setAsking(true), hidden: !data },
                    { icon: "comment", label: "Comments", run: () => setCommentsOpen(true), hidden: !commentable },
                    { icon: "share", label: "Share", run: () => setSharing(true), hidden: !data },
                    { icon: "save", label: "Save to phone", run: () => void handOn("keep"), hidden: !data },
                    { icon: "out", label: "Open in another app", run: () => void handOn("open"), hidden: !data },
                    { icon: "page", label: "Rename", run: () => setRenaming(true) },
                    { icon: "move", label: "Move", run: () => setMoving(true) },
                    { icon: "trash", label: "Delete", run: () => setDeleting(true) },
                ]}
            />
            {sharing ? <ShareSheet podId={podId} subject={{ kind: "file", path, label: title }} open onClose={() => setSharing(false)} /> : null}
            <NameSheet open={renaming} title={`Rename ${title}`} initial={item.name} action="Rename" busy={writes.busy === path} onClose={() => setRenaming(false)}
                onSubmit={async (name) => { const next = await writes.rename(item, name); if (next) { setRenaming(false); moved(next); } }} />
            <MoveSheet podId={podId} item={moving ? item : null} onClose={() => setMoving(false)}
                onMove={async (folder) => { setMoving(false); const next = await writes.move(item, folder); if (next) moved(next); }} />
            <ConfirmDelete item={deleting ? item : null} onClose={() => setDeleting(false)}
                onConfirm={async () => { setDeleting(false); leaving.current = true; if (await writes.remove(item)) back(); else leaving.current = false; }} />
            <AskSheet open={asking} lemmaName={lemmaName} onClose={() => setAsking(false)} onAsk={(text) => { setAsking(false); void ask(text); }} />
            {commentable ? (
                <CommentsSheet
                    podId={podId}
                    path={path}
                    lemmaName={lemmaName}
                    open={commentsOpen}
                    status={commentsStatus.data}
                    threads={threads}
                    loading={comments.isPending && commentsStatus.data === "ready"}
                    draft={draft}
                    active={active}
                    onClose={() => { setCommentsOpen(false); setDraft(null); }}
                    onDraftDone={() => setDraft(null)}
                    onActive={setActive}
                    onStartPage={() => setDraft("page")}
                />
            ) : null}
        </SafeAreaView>
    );
}

/** Ask the Lemma about this file, in its own conversation: a page it writes
 *  into, a file it reads. */
function AskSheet({ open, lemmaName, onClose, onAsk }: { open: boolean; lemmaName: string; onClose: () => void; onAsk: (text: string) => void }) {
    const [text, setText] = useState("");
    useEffect(() => { if (open) setText(""); }, [open]);
    return (
        <Sheet open={open} onClose={onClose} title={`Ask ${lemmaName}`}>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <View style={{ paddingHorizontal: 20, gap: 12 }}>
                    <TextInput
                        accessibilityLabel={`Ask ${lemmaName}`}
                        value={text}
                        onChangeText={setText}
                        placeholder="Shorten it, add a table of the numbers, check the dates…"
                        placeholderTextColor={color.ink3}
                        multiline
                        autoFocus
                        style={{ minHeight: 72, maxHeight: 160, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: color.line2, fontFamily: font.regular, fontSize: 16, color: color.ink, textAlignVertical: "top" }}
                    />
                    <Button title="Ask" disabled={!text.trim()} onPress={() => onAsk(text.trim())} />
                </View>
            </KeyboardAvoidingView>
        </Sheet>
    );
}
