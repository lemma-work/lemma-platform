"use client";

import "@/styles/desktop.css";
import { useState } from "react";
import { CloseIcon, FolderIcon } from "@/ui/icons";
import { folderLabel, type ConversationFolder } from "./folders";

/** The folder on this computer a conversation works in, beside the composer.
 *
 *  Drawn only where one can be chosen — a local install, in the desktop app.
 *  Self-contained so the composer does not have to know it exists: the pane
 *  owns the `useConversationFolder` state (it also adopts the choice once the
 *  conversation is created) and places this where it likes. */
export function FolderChip({ folder }: { folder: ConversationFolder }) {
    const [problem, setProblem] = useState<string | null>(null);
    if (!folder.available) return null;

    const choose = async () => {
        setProblem(null);
        try {
            await folder.bind();
        } catch (cause) {
            setProblem(cause instanceof Error ? cause.message : "That folder could not be used.");
        }
    };
    const clear = async () => {
        setProblem(null);
        try {
            await folder.unbind();
        } catch (cause) {
            setProblem(cause instanceof Error ? cause.message : "The folder could not be cleared.");
        }
    };

    return (
        <div className="folder-chip">
            <button
                className="folder-chip__pick"
                title={folder.folder ?? "Work in a folder on this computer"}
                onClick={() => void choose()}
            >
                <FolderIcon size={14} />
                <span>{folder.folder ? folderLabel(folder.folder) : "Choose a folder"}</span>
            </button>
            {folder.folder && (
                <button
                    className="folder-chip__clear icon-button"
                    aria-label="Stop using this folder"
                    title="Stop using this folder"
                    onClick={() => void clear()}
                >
                    <CloseIcon size={12} />
                </button>
            )}
            {problem && <span className="folder-chip__problem" role="alert">{problem}</span>}
        </div>
    );
}
