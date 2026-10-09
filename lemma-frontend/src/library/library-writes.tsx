"use client";

import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { lemma } from "@/session/client";
import { source, type LibraryItem } from "@/data";
import { describeSize, tooLarge } from "@/thread/attachments";
import {
    nameProblem,
    pathIn,
    renamedPath,
    withItem,
    withRenamedItem,
    withoutItem,
    type Pages,
} from "./library-cache";
import { readingProblem, withItemStatus } from "./file-status";
import { readDropped } from "./drop-entries";
import { rememberMade } from "@/data/made";

/** Making, renaming and removing things in the library.
 *
 *  Split from the listing because the listing is a read and these are not, and
 *  because a library that can only be read is a library the teammate writes to
 *  and you watch. The SDK has had `upload`, `folder.create`, `update` and
 *  `delete` all along.
 *
 *  Every one of these patches the cached pages rather than invalidating them.
 *  An invalidation here refetches every page the person has scrolled through to
 *  reflect one row.
 */

export function useLibraryWrites(podId: string, directory: string) {
    const cache = useQueryClient();
    const [busy, setBusy] = useState<string | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const sample = source.label === "sample";

    const key = ["library", podId, "files", directory];
    const patch = (change: (pages: Pages | undefined) => Pages | undefined) =>
        cache.setQueryData<Pages>(key, (pages) => change(pages) as Pages);

    /** A row for something that exists now but has not been listed yet. */
    function rowFor(name: string, path: string, kind: LibraryItem["kind"], detail: string, status?: string): LibraryItem {
        return { id: path, name, kind, path, updated: new Date().toISOString(), detail, status };
    }

    /** One file, into the folder it belongs in. Its own failure is reported
     *  and the rest carry on: a folder of thirty files should not stop at the
     *  fourth, and the fourth is still the one worth naming. */
    async function writeOne(file: File, at: string) {
        setBusy(file.name);
        try {
            /* Patched only into the listing it belongs to: a file dropped
               inside a folder is not a row of the folder it was dropped on. */
            const here = at === directory;
            if (sample) {
                const item = rowFor(file.name, pathIn(at, file.name), "file", describeSize(file.size));
                rememberMade(at, item);
                if (here) patch((pages) => withItem(pages, item));
            } else {
                const written = await lemma(podId).files.upload(file, {
                    name: file.name,
                    directoryPath: at,
                    searchEnabled: true,
                });
                if (here) patch((pages) => withItem(pages, rowFor(written.name ?? file.name, written.path, "file", describeSize(file.size), written.status)));
            }
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That file did not upload.");
        } finally {
            setBusy(null);
        }
    }

    async function upload(files: File[]) {
        if (files.length === 0) return;
        const big = files.filter(tooLarge);
        const rest = files.filter((file) => !tooLarge(file));
        setProblem(
            big.length === 0
                ? null
                : big.length === 1
                  ? big[0].name + " is too large to upload (" + describeSize(big[0].size) + ")."
                  : big.length + " files are too large to upload.",
        );
        for (const file of rest) await writeOne(file, directory);
    }

    /** A drop, which carries folders as readily as files. The tree is read
     *  first — a folder dropped from the desktop hands over the folder, not
     *  what is inside it — and then made and written. */
    async function drop(entries: readonly FileSystemEntry[], files: File[]) {
        if (entries.length === 0) { await upload(files); return; }
        setProblem(null);
        const tree = await readDropped(entries, directory);
        for (const folder of tree.folders) {
            /* The sample has no server to make a parent on the way to a file,
               so every folder is made there; live, only the folders an upload
               would not bring into being. */
            if (sample || !folder.holdsFile) await makeFolder(folder.path, folder.path.slice(folder.path.lastIndexOf("/") + 1));
        }
        for (const each of tree.files) await writeOne(each.file, each.directory);
    }

    /** A folder, made where it is named. The path rather than the parent and
     *  the name, because a dropped tree names folders at every depth. */
    async function makeFolder(path: string, name: string) {
        const parent = path.slice(0, path.lastIndexOf("/")) || "/";
        setBusy(name);
        try {
            const here = parent === directory;
            if (sample) {
                const item = rowFor(name, path, "folder", "Folder");
                rememberMade(parent, item);
                if (here) patch((pages) => withItem(pages, item));
            } else {
                const made = await lemma(podId).files.folder.create(name, { directoryPath: parent });
                if (here) patch((pages) => withItem(pages, rowFor(made.name ?? name, made.path, "folder", "Folder")));
            }
            return true;
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That folder was not created.");
            return false;
        } finally {
            setBusy(null);
        }
    }

    async function createFolder(name: string) {
        const bad = nameProblem(name);
        if (bad) { setProblem(bad); return false; }
        const clean = name.trim();
        setProblem(null);
        return makeFolder(pathIn(directory, clean), clean);
    }

    async function rename(item: LibraryItem, name: string) {
        const bad = nameProblem(name);
        if (bad) { setProblem(bad); return false; }
        const clean = name.trim();
        if (clean === item.name) return true;
        const next = renamedPath(item.path, clean);
        const before = cache.getQueryData<Pages>(key);
        setBusy(item.path);
        setProblem(null);
        patch((pages) => withRenamedItem(pages, item.path, clean, next));
        try {
            if (!sample) {
                await lemma(podId).files.update(item.path, { name: clean });
            }
            return true;
        } catch (failure) {
            cache.setQueryData(key, before);
            setProblem(failure instanceof Error ? failure.message : "That was not renamed.");
            return false;
        } finally {
            setBusy(null);
        }
    }

    async function remove(item: LibraryItem) {
        const before = cache.getQueryData<Pages>(key);
        setBusy(item.path);
        setProblem(null);
        patch((pages) => withoutItem(pages, item.path));
        try {
            if (!sample) {
                await lemma(podId).files.delete(item.path);
            }
        } catch (failure) {
            cache.setQueryData(key, before);
            setProblem(failure instanceof Error ? failure.message : "That was not deleted.");
        } finally {
            setBusy(null);
        }
    }

    /** Ask for a failed file to be read again. The row turns back to
     *  "Reading…" at once; the server answers with the status it settled on. */
    async function retry(item: LibraryItem) {
        const before = cache.getQueryData<Pages>(key);
        setBusy(item.path);
        setProblem(null);
        patch((pages) => withItemStatus(pages, item.path, "PENDING"));
        try {
            if (!sample) {
                const read = await lemma(podId).files.retryProcessing(item.path);
                patch((pages) => withItemStatus(pages, item.path, read.status));
            }
        } catch (failure) {
            cache.setQueryData(key, before);
            setProblem(failure instanceof Error ? failure.message : "That file was not queued again.");
        } finally {
            setBusy(null);
        }
    }

    /** Why a file could not be read. The listing leaves the error out (it can
     *  be long), so it is read on demand, for the one row asked about. */
    async function explain(item: LibraryItem): Promise<string> {
        if (sample) return readingProblem(null);
        try {
            return readingProblem((await lemma(podId).files.get(item.path)).last_processing_error);
        } catch {
            return readingProblem(null);
        }
    }

    return { busy, problem, clearProblem: () => setProblem(null), upload, drop, createFolder, rename, remove, retry, explain };
}

/** Ask before removing something.
 *
 *  Deleting a pod file is the one thing in here that cannot be undone from the
 *  app, and a folder takes everything under it. So this names what is going and
 *  says what that means, rather than asking "are you sure?" — which is a
 *  question nobody has ever read.
 */
export function ConfirmDelete({
    item,
    onCancel,
    onConfirm,
}: {
    item: LibraryItem;
    onCancel: () => void;
    onConfirm: () => void;
}) {
    const confirm = useRef<HTMLButtonElement | null>(null);
    return (
        <div className="library-confirm" role="alertdialog" aria-label={"Delete " + item.name}>
            <p>
                Delete <strong>{item.name}</strong>
                {item.kind === "folder" ? " and everything in it" : ""}? It is gone for everyone.
            </p>
            <div className="library-confirm__actions">
                <button ref={confirm} className="btn btn--danger" onClick={onConfirm}>
                    Delete
                </button>
                <button className="btn" onClick={onCancel}>
                    Keep it
                </button>
            </div>
        </div>
    );
}
