import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Platform, ScrollView, View } from "react-native";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useConverted, useFirstPage, useRows, useShownFile, useShownFiles } from "../areas/chat/hooks";
import type { ShownFile } from "../areas/chat/source";
import { filesRoutes } from "../areas/files/routes";
import { tablesRoutes } from "../areas/tables/routes";
import { workRoutes } from "../areas/work/routes";
import { readableName } from "../data/model";
import { routes } from "../data/routes";
import { useSession } from "../session/session";
import { SketchOf } from "../ui/app-tile";
import { AudioRow, DocumentCard, FileGlyph, Grown, GrownPage, PhotoGrid, PhotoViewer, Reading, SheetPreview, documentLine, readSheet, rectOf, sheetFromMarkdown, useGrow, useGrowInto, type Photo, type Rect } from "../ui/files-in-chat";
import { Icon, type IconName } from "../ui/icons";
import { Markdown } from "../ui/markdown";
import { Press } from "../ui/press";
import { Num, Text } from "../ui/text";
import { color } from "../ui/tokens";
import { WidgetFrame } from "../ui/web-content";
import { liveEnded, resourceLabel, type DisplayResource } from "@web/thread/display-resource";
import { Glyph, type GlyphName } from "./glyphs";
import { useLook } from "./look";
import { cellText, dataPreview, excerptOf, formatOf, readableSize, withoutFrontmatter } from "./previews";

/** What the Lemma put on screen: the web's `resource-card.tsx` and the views
 *  behind it (`widget-view.tsx`, `data-view.tsx`, `file-view.tsx`), for a
 *  phone. A widget is drawn, not described; a table or a query shows its
 *  rows; a file shows itself -- photos, a document's first page, a sheet's
 *  first rows, a recording to play, the start of the words -- and opens in
 *  Files; an app opens here; a live browser is watched until it ends; and
 *  the rest are links to where they live.
 *
 *  Nothing cuts to another screen: a widget and a table grow to the whole
 *  screen out of their card and shrink back into it, a photo opens out of
 *  itself, and a file grows into the reader that opens under it. The file
 *  pieces are `ui/files-in-chat`, shared with groups. */

const MONO = Platform.select({ ios: "Menlo", default: "monospace" });

/** Documents the server reads as pages, so a first page can be shown. */
const PAGED = new Set(["pdf", "doc", "docx", "ppt", "pptx", "odt", "odp", "rtf", "pages", "key"]);
/** Spreadsheets, whose rows the server reads into a table. */
const SHEETS = new Set(["xlsx", "xls", "ods", "numbers"]);
const ROWS = new Set(["csv", "tsv"]);

function extensionOf(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(dot + 1).toLowerCase() : "";
}

/** The frame every card but a picture and a widget sits in. */
function Card({ children, label }: { children: ReactNode; label: string }) {
    return (
        <View accessibilityLabel={label} style={{ alignSelf: "stretch", borderWidth: 1, borderColor: color.line2, borderRadius: 14, overflow: "hidden", backgroundColor: color.paper }}>
            {children}
        </View>
    );
}

/** A card's head: what it is, a word about it, and Open. */
function CardHead({ glyph, name, meta, onOpen, openLabel = "Open" }: { glyph: GlyphName | IconName; name: string; meta?: string; onOpen?: () => void; openLabel?: string }) {
    const look = useLook();
    const icon = (["table", "file", "app", "automation", "clock", "people", "chat"] as string[]).includes(glyph)
        ? <Icon name={glyph as IconName} size={15} tone={color.ink2} />
        : <Glyph name={glyph as GlyphName} size={15} tone={color.ink2} />;
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 40, paddingLeft: 12, paddingRight: onOpen ? 4 : 12, borderBottomWidth: 1, borderBottomColor: color.line }}>
            {icon}
            <Text size={14} weight="medium" numberOfLines={1} style={{ flexShrink: 1 }}>{name}</Text>
            {meta ? <Text size={12} tone={color.ink3} numberOfLines={1} style={{ flex: 1 }}>{meta}</Text> : <View style={{ flex: 1 }} />}
            {onOpen ? (
                <Press role="link" label={`${openLabel} ${name}`} onPress={onOpen} style={{ minHeight: 40, paddingHorizontal: 10, justifyContent: "center" }}>
                    <Text size={13.5} tone={look.ink}>{openLabel}</Text>
                </Press>
            ) : null}
        </View>
    );
}

/** A card that is one line: a glyph, a name, a word, where it goes. */
function LinkRow({ glyph, name, meta, go, onPress, disabled }: { glyph: GlyphName | IconName; name: string; meta: string; go?: ReactNode; onPress?: () => void; disabled?: boolean }) {
    const icon = (["table", "file", "app", "automation", "clock", "people", "chat"] as string[]).includes(glyph)
        ? <Icon name={glyph as IconName} size={17} tone={color.ink2} />
        : <Glyph name={glyph as GlyphName} size={17} tone={color.ink2} />;
    const inside = (
        <>
            <View style={{ width: 34, height: 34, borderRadius: 10, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>{icon}</View>
            <View style={{ flex: 1, minWidth: 0 }}>
                <Text size={14.5} numberOfLines={1}>{name}</Text>
                <Text size={12.5} tone={color.ink3} numberOfLines={1}>{meta}</Text>
            </View>
            {go}
        </>
    );
    const style = { alignSelf: "stretch" as const, flexDirection: "row" as const, alignItems: "center" as const, gap: 10, minHeight: 52, paddingVertical: 8, paddingHorizontal: 10, borderWidth: 1, borderColor: color.line2, borderRadius: 14, backgroundColor: color.paper };
    if (!onPress || disabled) return <View style={style}>{inside}</View>;
    return <Press role="link" label={`${name}, ${meta}`} onPress={onPress} style={style}>{inside}</Press>;
}

function Waiting({ say }: { say: string }) {
    return (
        <View accessibilityLabel={say} style={{ flexDirection: "row", alignItems: "center", gap: 8, padding: 12 }}>
            <ActivityIndicator size="small" color={color.ink3} />
            <Text size={13} tone={color.ink3}>{say}</Text>
        </View>
    );
}

/** A row of cells, numbers in Inter. */
function Cells({ cells, width, last }: { cells: string[]; width: number; last: boolean }) {
    return (
        <View style={{ flexDirection: "row", borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}>
            {cells.map((cell, at) => (
                /^[$€£-]?\d[\d,.:]*%?$/.test(cell)
                    ? <Num key={at} numberOfLines={1} style={{ width, paddingHorizontal: 10, paddingVertical: 8, fontSize: 13, color: color.ink }}>{cell}</Num>
                    : <Text key={at} size={13} numberOfLines={1} style={{ width, paddingHorizontal: 10, paddingVertical: 8 }}>{cell}</Text>
            ))}
        </View>
    );
}

/* ── a table or a query, as a few rows ─────────────────────────────────── */

export function DataView({ podId, name, sql }: { podId: string; name?: string; sql?: string }) {
    const router = useRouter();
    const look = useLook();
    const rows = useRows(podId, name, sql);
    const label = name ? readableName(name) : "Query result";
    const items = rows.data?.items ?? [];
    const preview = dataPreview(items, rows.data?.truncated === true);
    const [sqlOpen, setSqlOpen] = useState(false);
    const grow = useGrow();
    const open = items.length > 0 ? () => void grow.grow() : name ? () => router.push(tablesRoutes.table(podId, name) as never) : undefined;
    return (
        <>
            <View ref={grow.card} collapsable={false} style={{ alignSelf: "stretch" }}>
                <Card label={label}>
                    <CardHead glyph="table" name={label} meta={sql ? "read-only query" : "table"} onOpen={open} />
                    {sql ? (
                        <Press label={sqlOpen ? "Hide the query" : "Show the query"} onPress={() => setSqlOpen((was) => !was)} style={{ paddingHorizontal: 12, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: color.chrome }}>
                            <Text numberOfLines={sqlOpen ? undefined : 2} style={{ fontFamily: MONO, fontSize: 12, lineHeight: 17, color: color.ink2 }}>{sql}</Text>
                        </Press>
                    ) : null}
                    {rows.isPending ? <Waiting say={`Reading ${label}…`} /> : null}
                    {rows.isError ? (
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, padding: 12 }}>
                            <Text size={13} tone={color.amber} style={{ flex: 1 }}>Could not read {label}.</Text>
                            <Press label="Try again" onPress={() => void rows.refetch()} style={{ minHeight: 32, justifyContent: "center" }}><Text size={13} tone={look.ink}>Try again</Text></Press>
                        </View>
                    ) : null}
                    {!rows.isPending && !rows.isError && preview.rows.length === 0 ? <Text size={13} tone={color.ink3} style={{ padding: 12 }}>No rows came back.</Text> : null}
                    {preview.rows.length > 0 ? (
                        <ScrollView horizontal showsHorizontalScrollIndicator={false} nestedScrollEnabled>
                            <View>
                                <View style={{ flexDirection: "row", borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: color.chrome }}>
                                    {preview.columns.map((column) => (
                                        <Text key={column} size={12} tone={color.ink3} numberOfLines={1} style={{ width: 132, paddingHorizontal: 10, paddingVertical: 7 }}>{column}</Text>
                                    ))}
                                </View>
                                {preview.rows.map((row, index) => <Cells key={index} cells={row} width={132} last={index === preview.rows.length - 1} />)}
                            </View>
                        </ScrollView>
                    ) : null}
                    {preview.footnote || preview.capped ? (
                        /* What the preview is not showing, said plainly: a capped
                           result that looks complete is how a wrong number gets
                           quoted out loud. */
                        <Text size={12} tone={preview.capped ? color.amber : color.ink3} style={{ paddingHorizontal: 12, paddingVertical: 8, borderTopWidth: 1, borderTopColor: color.line }}>
                            {[preview.footnote, preview.capped ? "cut short by the row cap: there are more rows than this" : ""].filter(Boolean).join(" · ")}
                        </Text>
                    ) : null}
                </Card>
            </View>
            {grow.shown ? (
                <Grown from={grow.from} back={grow.back} open={grow.open} onClosed={grow.closed} onRequestClose={() => void grow.shrink()}>
                    <GrownPage
                        title={label}
                        onClose={() => void grow.shrink()}
                        action={name ? (
                            <Press role="link" label={`Open the table ${label}`} onPress={() => { grow.closed(); router.push(tablesRoutes.table(podId, name) as never); }} style={{ minHeight: 44, paddingHorizontal: 12, justifyContent: "center" }}>
                                <Text size={14} tone={look.ink}>Open table</Text>
                            </Press>
                        ) : undefined}
                    >
                        <FullTable items={items} capped={rows.data?.truncated === true} sql={sql} />
                    </GrownPage>
                </Grown>
            ) : null}
        </>
    );
}

/** Every row and column the card read, with room to read them. */
function FullTable({ items, capped, sql }: { items: readonly Record<string, unknown>[]; capped: boolean; sql?: string }) {
    const columns = useMemo(() => Array.from(new Set(items.flatMap((row) => Object.keys(row)))), [items]);
    return (
        <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
            {sql ? <Text style={{ fontFamily: MONO, fontSize: 12, lineHeight: 17, color: color.ink2, padding: 14, backgroundColor: color.chrome }}>{sql}</Text> : null}
            <ScrollView horizontal showsHorizontalScrollIndicator={false}>
                <View>
                    <View style={{ flexDirection: "row", borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: color.chrome }}>
                        {columns.map((column) => <Text key={column} size={12.5} tone={color.ink3} numberOfLines={1} style={{ width: 150, paddingHorizontal: 10, paddingVertical: 9 }}>{column}</Text>)}
                    </View>
                    {items.map((row, index) => <Cells key={index} cells={columns.map((column) => cellText(row[column]))} width={150} last={index === items.length - 1} />)}
                </View>
            </ScrollView>
            <Text size={12.5} tone={capped ? color.amber : color.ink3} style={{ paddingHorizontal: 14, paddingTop: 12 }}>
                {items.length} row{items.length === 1 ? "" : "s"}{capped ? " · cut short by the row cap: there are more rows than this" : ""}
            </Text>
        </ScrollView>
    );
}

/* ── photos ─────────────────────────────────────────────────────────────── */

/** Pictures shown together, as one grid of the files they are. */
export function PhotoRun({ podId, paths, label }: { podId: string; paths: readonly string[]; label?: string }) {
    const files = useShownFiles(podId, paths);
    const photos: Photo[] = paths.map((path, index) => {
        const data = files[index]?.data;
        return { key: path, uri: data?.url ?? null, name: data?.name ?? path.split("/").pop() ?? path, type: data?.mime || undefined };
    });
    return <PhotoGrid photos={photos} label={label} />;
}

/** One picture, in its own shape, opening whole screen out of itself. */
function OnePhoto({ data, onBroke }: { data: ShownFile; onBroke: () => void }) {
    const frame = useRef<View>(null);
    const [ratio, setRatio] = useState(4 / 3);
    const [viewing, setViewing] = useState<Rect | null | false>(false);
    const photo: Photo = { key: data.path, uri: data.url, name: data.name, type: data.mime || undefined };
    return (
        <View style={{ alignSelf: "stretch", gap: 4 }}>
            <Press label={`${data.name}. Open`} onPress={() => void rectOf(frame.current).then(setViewing)}>
                <View ref={frame} collapsable={false}>
                    <Image
                        accessibilityLabel={data.name}
                        source={{ uri: data.url ?? undefined }}
                        contentFit="cover"
                        onLoad={(event) => {
                            const { width, height } = event.source;
                            if (width > 0 && height > 0) setRatio(Math.min(2, Math.max(0.75, width / height)));
                        }}
                        onError={onBroke}
                        style={{ width: "100%", aspectRatio: ratio, borderRadius: 14, backgroundColor: color.chrome }}
                    />
                </View>
            </Press>
            <Text size={12.5} tone={color.ink3} numberOfLines={1}>{data.name}</Text>
            {viewing !== false ? <PhotoViewer photos={[photo]} start={0} from={viewing} onClose={() => setViewing(false)} /> : null}
        </View>
    );
}

/* ── a file ─────────────────────────────────────────────────────────────── */

export function FileView({ podId, path }: { podId: string; path: string }) {
    const router = useRouter();
    const look = useLook();
    const file = useShownFile(podId, path);
    const [broke, setBroke] = useState(false);
    const name = path.split("/").filter(Boolean).pop() ?? path;
    const extension = extensionOf(file.data?.name ?? name);
    const paged = (file.data?.kind === "pdf" || PAGED.has(extension)) && Boolean(file.data);
    const firstPage = useFirstPage(podId, file.data?.path ?? path, paged);
    const sheet = SHEETS.has(extension) && Boolean(file.data);
    const converted = useConverted(podId, file.data?.path ?? path, sheet);
    const open = () => router.push(filesRoutes.file(podId, file.data?.path ?? path) as never);
    const into = useGrowInto(open);

    if (file.isPending || file.isError || !file.data) {
        return (
            <LinkRow
                glyph="file"
                name={name}
                meta={file.isPending ? "Reading the file…" : "Unable to read this file. It may have moved, or you may not have access."}
                onPress={file.isError ? () => void file.refetch() : undefined}
                go={file.isError ? <Text size={13} tone={look.ink}>Try again</Text> : file.isPending ? <ActivityIndicator size="small" color={color.ink3} /> : null}
            />
        );
    }
    const data = file.data;
    const size = readableSize(data.size);
    const meta = [data.kind === "binary" ? formatOf(data.name) : data.kind, size].filter(Boolean).join(" · ");
    /* The card grows into the reader, which opens under it. */
    const growing = (card: ReactNode, preview: string | null = null) => (
        <>
            <View ref={into.card} collapsable={false} style={{ alignSelf: "stretch" }}>{card}</View>
            {into.overlay(<Reading name={data.name} preview={preview} glyph="page" />)}
        </>
    );

    if (data.kind === "image" && data.url && !broke) return <OnePhoto data={data} onBroke={() => setBroke(true)} />;

    if (data.kind === "audio" && data.url) return <AudioRow uri={data.url} name={data.name} seed={data.path} tone={look.ink} onTone={look.onInk} />;

    if (paged) {
        return <DocumentCard name={data.name} line={documentLine(data.name, data.pages, size)} preview={firstPage.data ?? null} loading={firstPage.isPending} onOpen={open} />;
    }

    /* A CSV is read here; a spreadsheet as the server read it. */
    if ((ROWS.has(extension) && data.text !== null) || sheet) {
        const rows = ROWS.has(extension) && data.text !== null ? readSheet(data.text, { name: data.name }) : converted.data ? sheetFromMarkdown(converted.data) : null;
        if (rows || (sheet && converted.isPending)) {
            return growing(<SheetPreview name={data.name} sheet={rows} loading={!rows} tone={look.ink} onOpen={() => void into.grow()} />);
        }
        return <DocumentCard name={data.name} line={documentLine(data.name, null, size)} preview={null} glyph="sheet" onOpen={open} />;
    }

    /* Words: the start of them, and Open for the rest. Never editable here:
       in a chat this is one thing in a column of things. */
    if ((data.kind === "markdown" || data.kind === "text") && data.text !== null) {
        const body = data.kind === "markdown" ? withoutFrontmatter(data.text) : data.text;
        const excerpt = excerptOf(body);
        return growing(
            <Card label={data.name}>
                <CardHead glyph="file" name={data.name} meta={meta} onOpen={() => void into.grow()} />
                <View style={{ paddingHorizontal: 12, paddingVertical: 10, gap: 4 }}>
                    {data.kind === "markdown"
                        ? <Markdown text={excerpt.text} size={14} copyable={false} />
                        : <Text selectable style={{ fontFamily: MONO, fontSize: 12, lineHeight: 17, color: color.ink }}>{excerpt.text}</Text>}
                    {excerpt.cut ? (
                        <Press role="link" label={`Read all of ${data.name}`} onPress={() => void into.grow()} style={{ alignSelf: "flex-start", minHeight: 30, justifyContent: "center" }}>
                            <Text size={13} tone={look.ink}>Read all of it</Text>
                        </Press>
                    ) : null}
                </View>
            </Card>,
        );
    }

    /* Everything else is a row that grows into Files, which knows how to
       show a video or a page, and to hand anything else to another app. */
    return growing(
        <LinkRow
            glyph={data.kind === "image" ? "image" : "file"}
            name={data.name}
            meta={data.note ?? meta}
            onPress={() => void into.grow()}
            go={<Icon name="forward" size={14} width={1.9} tone={color.ink3} />}
        />,
    );
}

/* ── a widget ───────────────────────────────────────────────────────────── */

function WidgetView({ podId, conversationId, toolCallId, resource, onCompose }: {
    podId: string;
    conversationId: string | null;
    toolCallId?: string;
    resource: DisplayResource;
    onCompose: (text: string, newConversation: boolean) => void;
}) {
    const { mode } = useSession();
    const label = resourceLabel(resource);
    const grow = useGrow();
    /* A minted address is the real widget, with live data. It needs both
       ids; with neither there is only what the call carried. */
    if (mode === "live" && conversationId && toolCallId) {
        const frame = <WidgetFrame podId={podId} conversationId={conversationId} toolCallId={toolCallId} title={label} fallback={null} onCompose={onCompose} />;
        return (
            /* Stretched: a WebView has no width of its own, and the reply's
               column lines its children up at the start. */
            <View style={{ alignSelf: "stretch", gap: 2 }}>
                <View ref={grow.card} collapsable={false}>{frame}</View>
                <Press label={`${label}, whole screen`} onPress={() => void grow.grow()} hitSlop={6} style={{ alignSelf: "flex-end", flexDirection: "row", alignItems: "center", gap: 5, minHeight: 30, paddingHorizontal: 4 }}>
                    <FileGlyph name="expand" size={14} tone={color.ink3} />
                    <Text size={12.5} tone={color.ink3}>Whole screen</Text>
                </Press>
                {grow.shown ? (
                    <Grown from={grow.from} back={grow.back} open={grow.open} onClosed={grow.closed} onRequestClose={() => void grow.shrink()}>
                        <GrownPage title={label} onClose={() => void grow.shrink()}>
                            <ScrollView contentContainerStyle={{ padding: 12 }}>
                                <WidgetFrame podId={podId} conversationId={conversationId} toolCallId={toolCallId} title={label} fallback={null} onCompose={(text, fresh) => { grow.closed(); onCompose(text, fresh); }} />
                            </ScrollView>
                        </GrownPage>
                    </Grown>
                ) : null}
            </View>
        );
    }
    return (
        <Card label={label}>
            <CardHead glyph="app" name={label} meta="widget" />
            <Text size={13.5} tone={color.ink2} style={{ padding: 12, lineHeight: 19 }}>
                {mode === "live" ? "This widget cannot be drawn here." : "A widget, drawn live with your own Lemmas. Sample data has nothing behind it."}
            </Text>
        </Card>
    );
}

/* ── a live browser ─────────────────────────────────────────────────────── */

function BrowserView({ resource }: { resource: DisplayResource }) {
    const look = useLook();
    /* Redrawn when its address runs out, so it says Ended rather than
       keeping a Watch that no longer works. */
    const [, tick] = useState(0);
    useEffect(() => {
        if (!resource.liveUntil) return;
        const wait = Date.parse(resource.liveUntil) - Date.now();
        if (!Number.isFinite(wait) || wait <= 0) return;
        const timer = setTimeout(() => tick((n) => n + 1), Math.min(wait + 50, 2 ** 31 - 1));
        return () => clearTimeout(timer);
    }, [resource.liveUntil]);
    const ended = liveEnded(resource);
    let url: string | null = null;
    try {
        const protocol = resource.liveUrl ? new URL(resource.liveUrl).protocol : "";
        url = !ended && (protocol === "http:" || protocol === "https:") ? resource.liveUrl ?? null : null;
    } catch {
        url = null;
    }
    const label = resourceLabel(resource);
    return (
        <LinkRow
            glyph="eye"
            name={label}
            meta={ended ? "live browser · ended" : url ? "live browser" : "live browser · not shown here"}
            onPress={url ? () => void WebBrowser.openBrowserAsync(url as string).catch(() => undefined) : undefined}
            go={url ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 4 }}>
                    <Text size={13.5} tone={look.ink}>Watch</Text>
                    <Glyph name="external" size={13} tone={look.ink} />
                </View>
            ) : ended ? <Text size={13} tone={color.ink3}>Ended</Text> : null}
        />
    );
}

/* ── the card for each kind ─────────────────────────────────────────────── */

export function ResourceCard({ resource, podId, conversationId, toolCallId, onCompose }: {
    resource: DisplayResource;
    podId: string;
    conversationId: string | null;
    toolCallId?: string;
    onCompose: (text: string, newConversation: boolean) => void;
}) {
    const router = useRouter();
    const look = useLook();
    const label = resourceLabel(resource);
    switch (resource.type) {
        case "WIDGET":
            return <WidgetView podId={podId} conversationId={conversationId} toolCallId={toolCallId} resource={resource} onCompose={onCompose} />;
        case "BROWSER":
            return <BrowserView resource={resource} />;
        case "FILE":
            if (resource.path) return <FileView podId={podId} path={resource.path} />;
            break;
        case "TABLE":
            return <DataView podId={podId} name={resource.name} sql={resource.query} />;
        case "APP":
            if (resource.name) {
                const name = resource.name;
                return (
                    <Press role="link" label={`Open ${readableName(name)}`} onPress={() => router.push(routes.app(podId, name) as never)} style={{ alignSelf: "stretch", gap: 6 }}>
                        <SketchOf name={name} height={96} />
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                            <Text size={14} style={{ flex: 1 }} numberOfLines={1}>{readableName(name)}</Text>
                            <Text size={13.5} tone={look.ink}>Open</Text>
                        </View>
                    </Press>
                );
            }
            break;
        case "WORKFLOW":
            return <LinkRow glyph="automation" name={resource.name ? readableName(resource.name) : label} meta="workflow" onPress={resource.name ? () => router.push(workRoutes.workflow(podId, resource.name as string) as never) : () => router.push(workRoutes.runs(podId) as never)} go={<Icon name="forward" size={14} width={1.9} tone={color.ink3} />} />;
        case "SCHEDULE":
            return <LinkRow glyph="clock" name={resource.name ? readableName(resource.name) : label} meta="schedule" onPress={() => router.push(workRoutes.runs(podId) as never)} go={<Icon name="forward" size={14} width={1.9} tone={color.ink3} />} />;
        case "AGENT":
            return <LinkRow glyph="agent" name={resource.name ? readableName(resource.name) : label} meta="agent" onPress={resource.name ? () => router.push(workRoutes.agent(podId, resource.name as string) as never) : undefined} go={resource.name ? <Icon name="forward" size={14} width={1.9} tone={color.ink3} /> : null} />;
        case "FUNCTION":
            return <LinkRow glyph="code" name={label} meta="function" />;
    }
    /* A query with no table, or a card with nothing to point at. */
    if (resource.query) return <DataView podId={podId} sql={resource.query} />;
    return <LinkRow glyph="file" name={label} meta={resource.type.toLowerCase()} />;
}
