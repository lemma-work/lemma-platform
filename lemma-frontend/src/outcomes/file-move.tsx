"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { pathIn } from "@/library/library-cache";
import { isForbidden } from "@/session/auth-state";
import { Modal } from "@/shell/modal";
import { ChevronRightIcon } from "@/ui/icons";
import { LoadingIndicator } from "@/ui/loading";
import { crumbsOf, folderPath, isLemmasOwn, parentOf, type FileItem, type Whose } from "./file-kinds";
import { KindMark } from "./file-sheet";
import { useFolder } from "./files-data";

/** Moving a file or folder to another folder, picked by walking the
 *  folders the way the page does: the path at the top, the folders in the
 *  one you are in under it, Move here at the foot.
 *
 *  Within the files it is in -- everyone's, or the reader's own. Moving a
 *  file between the two would change who can see it, which is a bigger
 *  thing than where it sits and not something to do with a folder picker.
 *  The dialog stays open until the move has an answer, and a refusal is
 *  said in it. */
export function MoveDialog({ podId, item, whose, onClose, onMoved }: {
    podId: string;
    item: FileItem;
    whose: Whose;
    onClose: () => void;
    onMoved: () => void;
}) {
    const cache = useQueryClient();
    const self = folderPath(item.path);
    const from = parentOf(item.path);
    const [at, setAt] = useState(from);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const listing = useFolder(podId, at, { poll: false });
    const folders = (listing.data?.pages.flatMap((page) => page.items) ?? [])
        .filter((one) => one.kind === "folder" && !isLemmasOwn(one.path) && folderPath(one.path) !== self)
        .sort((a, b) => a.name.localeCompare(b.name));
    const steps = crumbsOf(at, whose);
    const here = steps[steps.length - 1].label;

    async function move() {
        setBusy(true);
        setProblem(null);
        try {
            await source.renameFile(podId, self, pathIn(at, item.name));
            void cache.invalidateQueries({ queryKey: ["library", podId, "files"] });
            onMoved();
        } catch (failure) {
            const status = (failure as { statusCode?: number } | null)?.statusCode;
            setProblem(
                status === 409 ? "Something called " + item.name + " is already in " + here + "."
                    : isForbidden(failure) ? "You can’t move this one. Whoever looks after these files can."
                        : "That didn’t move. Try again.",
            );
        } finally {
            setBusy(false);
        }
    }

    return (
        <Modal
            title={"Move " + item.name}
            subtitle="Pick the folder it goes in."
            onClose={() => { if (!busy) onClose(); }}
            footer={<>
                <button type="button" className="b b--l b--secondary" disabled={busy} onClick={onClose}>Cancel</button>
                <button type="button" className="b b--l b--primary" disabled={busy || at === from} onClick={() => void move()}>
                    {busy ? "Moving…" : at === from ? "It’s here now" : "Move to " + here}
                </button>
            </>}
        >
            <div className="ox-files-move">
                <nav className="ox-files-crumbs" aria-label="Folder to move it to">
                    {steps.map((step, index) => (
                        <span key={step.path} className="ox-files-crumbs__step">
                            {index > 0 && <ChevronRightIcon size={13} aria-hidden="true" />}
                            <button type="button" aria-current={index === steps.length - 1 ? "true" : undefined} disabled={busy} onClick={() => setAt(step.path)}>{step.label}</button>
                        </span>
                    ))}
                </nav>
                {listing.isPending ? (
                    <p className="ox-files-quiet"><LoadingIndicator inline label="Reading the folders" /></p>
                ) : listing.isError ? (
                    <p className="ox-files-quiet" role="alert">
                        I couldn’t read the folders here.{" "}
                        <button type="button" className="b b--s b--secondary" onClick={() => void listing.refetch()}>Try again</button>
                    </p>
                ) : folders.length === 0 ? (
                    <p className="ox-files-quiet">No folders in {here}. It can go here.</p>
                ) : (
                    <ul className="ox-files-move__list" aria-label={"Folders in " + here}>
                        {folders.map((folder) => (
                            <li key={folder.path}>
                                <button type="button" className="ox-files-move__row" disabled={busy} onClick={() => setAt(folderPath(folder.path))}>
                                    <KindMark mark="folder" />
                                    <span className="ox-files-move__name">{folder.name}</span>
                                    <ChevronRightIcon size={14} aria-hidden="true" />
                                </button>
                            </li>
                        ))}
                    </ul>
                )}
                {listing.hasNextPage && (
                    <button type="button" className="b b--s b--secondary" disabled={listing.isFetchingNextPage} onClick={() => void listing.fetchNextPage()}>
                        {listing.isFetchingNextPage ? "Reading more…" : "Show more folders"}
                    </button>
                )}
                {problem && <p className="kd-problem" role="alert">{problem}</p>}
            </div>
        </Modal>
    );
}
