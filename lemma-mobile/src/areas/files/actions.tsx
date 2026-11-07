import { useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { pickFiles, pickPhotos, takePhoto, type PickedFile } from "../../data/io";
import { Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { FilesIcon } from "./glyphs";
import { useFilesSource, useNewPage, useWrites } from "./hooks";
import { isPage, isPersonal, kindOf, nameOf, pageFolderFor, titleOf } from "./model";
import { ConfirmDelete, ItemSheet, MoveSheet, NameSheet, SheetRow } from "./parts";
import { filesRoutes } from "./routes";
import { keepCopy, openElsewhere } from "./save";
import { ShareSheet } from "../more/share/sheet";
import type { LibraryItem } from "./source";
import { fileReading } from "@web/library/file-status";
import { PAGE_TEMPLATES, type PageTemplate } from "@web/docpages/templates";

/** Everything a folder lets you do, for a screen that lists one: open a row,
 *  hold it for its actions, and the + for adding. The screen draws the rows;
 *  this holds the sheets, the writes and the one line that reports them. */
export function useFileActions({ podId, lemmaName, directory }: { podId: string; lemmaName: string; directory: string }) {
    const router = useRouter();
    const source = useFilesSource();
    const writes = useWrites(podId, directory);
    const newPage = useNewPage(podId, directory);
    const [held, setHeld] = useState<LibraryItem | null>(null);
    const [renaming, setRenaming] = useState<LibraryItem | null>(null);
    const [moving, setMoving] = useState<LibraryItem | null>(null);
    const [deleting, setDeleting] = useState<LibraryItem | null>(null);
    const [sharing, setSharing] = useState<LibraryItem | null>(null);
    const [adding, setAdding] = useState(false);
    const [templates, setTemplates] = useState(false);
    const [naming, setNaming] = useState(false);
    const [note, setNote] = useState<string | null>(null);
    const [failed, setFailed] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);

    const say = (text: string | null) => { setNote(text); setFailed(null); };
    const fail = (failure: unknown, fallback: string) => { setNote(null); setFailed(failure instanceof Error && failure.message ? failure.message : fallback); };

    const open = (item: LibraryItem) => {
        router.push((item.kind === "folder" ? filesRoutes.folder(podId, item.path) : filesRoutes.file(podId, item.path)) as never);
    };

    async function upload(pick: () => Promise<PickedFile[]>) {
        say(null);
        try {
            const files = await pick();
            if (files.length === 0) return;
            const made = await writes.upload(files);
            if (made.length > 0) say(made.length === 1 ? `${titleOf(made[0])} is in.` : `${made.length} files are in.`);
        } catch (failure) {
            fail(failure, "Those files could not be added.");
        }
    }

    async function makePage(template: PageTemplate | null) {
        setTemplates(false);
        setAdding(false);
        say(null);
        try {
            const path = await newPage.mutateAsync(template ? { base: template.file, text: template.body(lemmaName, new Date()) } : { base: "Untitled", text: "# Untitled\n\n" });
            router.push(filesRoutes.file(podId, path) as never);
        } catch (failure) {
            fail(failure, "That page could not be made.");
        }
    }

    async function handOn(item: LibraryItem, how: "keep" | "open") {
        setBusy(item.path);
        say(null);
        try {
            const uri = await source.toPhone(podId, item.path);
            const mime = item.mime || "application/octet-stream";
            if (how === "keep") {
                const done = await keepCopy(uri, item.name, mime);
                if (done) say(done);
            } else {
                await openElsewhere(uri, mime);
            }
        } catch (failure) {
            fail(failure, `${item.name} could not be opened.`);
        } finally {
            setBusy(null);
        }
    }

    async function explain(item: LibraryItem) {
        setNote(null);
        setFailed(await writes.explain(item));
    }

    const unread = held && held.kind === "file" ? fileReading(held.status)?.state === "failed" : false;
    const viewable = held ? kindOf(held.mime ?? "", held.name) !== "binary" : false;
    const sheets = (
        <>
            <ItemSheet
                item={held}
                onClose={() => setHeld(null)}
                actions={held ? [
                    { icon: held.kind === "folder" ? "folder" : isPage(held.path) ? "page" : "file", label: "Open", run: () => open(held) },
                    { icon: "spark", label: "Read it again", detail: "Lemma could not read it the first time", run: () => void writes.retry(held), hidden: !unread },
                    { icon: "comment", label: "Why it could not be read", run: () => void explain(held), hidden: !unread },
                    { icon: "share", label: "Share", run: () => setSharing(held), hidden: held.kind === "folder" },
                    { icon: "save", label: "Save to phone", run: () => void handOn(held, "keep"), hidden: held.kind === "folder" },
                    { icon: "out", label: "Open in another app", run: () => void handOn(held, "open"), hidden: held.kind === "folder" || viewable },
                    { icon: "page", label: "Rename", run: () => setRenaming(held) },
                    { icon: "move", label: "Move", run: () => setMoving(held) },
                    { icon: "trash", label: "Delete", run: () => setDeleting(held) },
                ] : []}
            />
            <NameSheet
                open={renaming !== null}
                title={renaming ? `Rename ${titleOf(renaming)}` : "Rename"}
                initial={renaming?.name ?? ""}
                action="Rename"
                busy={writes.busy === renaming?.path}
                onClose={() => setRenaming(null)}
                onSubmit={async (name) => {
                    const item = renaming;
                    if (!item) return;
                    const done = await writes.rename(item, name);
                    if (done) setRenaming(null);
                }}
            />
            <NameSheet
                open={naming}
                title="New folder"
                initial=""
                action="Make it"
                busy={writes.busy !== null && naming}
                onClose={() => setNaming(false)}
                onSubmit={async (name) => {
                    if (await writes.createFolder(name)) setNaming(false);
                }}
            />
            <MoveSheet
                podId={podId}
                item={moving}
                onClose={() => setMoving(null)}
                onMove={async (folder) => {
                    const item = moving;
                    setMoving(null);
                    if (item && (await writes.move(item, folder))) say(`${titleOf(item)} is in ${folder === "/" ? "Shared" : folder === "/me" ? "Mine" : nameOf(folder)} now.`);
                }}
            />
            <ConfirmDelete item={deleting} onClose={() => setDeleting(null)} onConfirm={() => { const item = deleting; setDeleting(null); if (item) void writes.remove(item); }} />
            {sharing ? <ShareSheet podId={podId} subject={{ kind: "file", path: sharing.path, label: titleOf(sharing) }} open onClose={() => setSharing(null)} /> : null}
            <Sheet open={adding} onClose={() => setAdding(false)} title={`Add to ${directory === "/" ? "shared files" : directory === "/me" ? "your files" : nameOf(directory)}`}>
                <View>
                    <SheetRow icon="page" label="New page" detail={directory === "/" ? "In Pages" : isPersonal(directory) ? "Only you can see it" : undefined} onPress={() => void makePage(null)} />
                    <SheetRow icon="spark" label="From a template" onPress={() => { setAdding(false); setTemplates(true); }} />
                    <SheetRow icon="camera" label="Take a photo" onPress={() => { setAdding(false); void upload(async () => { const one = await takePhoto(); return one ? [one] : []; }); }} />
                    <SheetRow icon="image" label="Photos and videos" onPress={() => { setAdding(false); void upload(pickPhotos); }} />
                    <SheetRow icon="upload" label="Files" onPress={() => { setAdding(false); void upload(pickFiles); }} />
                    <SheetRow icon="folder" label="New folder" onPress={() => { setAdding(false); setNaming(true); }} />
                </View>
            </Sheet>
            <Sheet open={templates} onClose={() => setTemplates(false)} title={`Start a page${pageFolderFor(directory) === "/pages" ? " in Pages" : ""}`}>
                <View>
                    {PAGE_TEMPLATES.map((template) => (
                        <Press key={template.id} label={template.title} disabled={newPage.isPending} onPress={() => void makePage(template)} style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: TOUCH + 10, paddingHorizontal: 20 }}>
                            <FilesIcon name="page" size={20} tone={color.ink} />
                            <View style={{ flex: 1, minWidth: 0 }}>
                                <Text size={16}>{template.title}</Text>
                                <Text size={13} tone={color.ink3}>{template.blurb}</Text>
                            </View>
                        </Press>
                    ))}
                </View>
            </Sheet>
        </>
    );

    const working = writes.busy ?? busy ?? (newPage.isPending ? "a new page" : null);
    const status = working ? (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 8, paddingHorizontal: 4 }}>
            <ActivityIndicator size="small" color={color.violet} />
            <Text size={13.5} tone={color.ink2} numberOfLines={1}>Working on {working.startsWith("/") ? titleOf({ kind: "file", name: nameOf(working) }) : working}…</Text>
        </View>
    ) : null;

    return {
        writes,
        open,
        hold: setHeld,
        add: () => { say(null); setAdding(true); },
        sheets,
        status,
        note,
        /* What did not happen: a write the server refused, or a file that
           could not be handed on. */
        problem: writes.problem ?? failed,
        clearProblem: () => { writes.clearProblem(); setFailed(null); },
    };
}
