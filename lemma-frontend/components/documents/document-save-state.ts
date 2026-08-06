/**
 * Who saves a document, and what the reader is told about it.
 *
 * Prose writes itself back the way an agent's prompt does — you type, you pause,
 * it is on disk — because a Save button on a paragraph is a chore, and losing a
 * paragraph to a closed tab is worse. Source files do not: a half-typed JSON
 * object is not a state anyone wants persisted on a 700ms timer, and the person
 * editing one is used to deciding when it lands.
 *
 * Split out from the viewer because it is a set of pure decisions the component
 * around it makes hard to see, and because this is the file that changes when a
 * new preview type arrives.
 */

/** How long typing has to stop before an autosaved document is written. */
export const AUTOSAVE_DELAY_MS = 700;

export type DocumentSaveState = 'idle' | 'saving' | 'saved' | 'error';

/**
 * Preview types that write themselves back.
 *
 * Markdown only. The editor round-trips through TipTap, so saving rewrites the
 * body in TipTap's markdown dialect — harmless for prose, and the reason this
 * cannot quietly extend to files where the exact bytes are the point.
 */
export function isAutosavedPreviewType(previewType: string): boolean {
    return previewType === 'markdown';
}

export type DocumentSaveStatus = {
    label: string;
    /** Errors are the only state worth colouring; the rest is a whisper. */
    tone: 'quiet' | 'error';
};

/**
 * The one line of status an autosaved document shows, or nothing.
 *
 * "Saved" only appears after a save this session — a document you have only
 * read has nothing to report, and announcing it would imply you changed
 * something. Unsaved edits say so rather than staying silent, so a stalled
 * network is visible before you close the tab.
 */
export function describeAutosaveStatus({
    state,
    hasUnsavedChanges,
}: {
    state: DocumentSaveState;
    hasUnsavedChanges: boolean;
}): DocumentSaveStatus | null {
    if (state === 'error') return { label: "Couldn't save", tone: 'error' };
    if (state === 'saving') return { label: 'Saving…', tone: 'quiet' };
    if (hasUnsavedChanges) return { label: 'Unsaved changes', tone: 'quiet' };
    if (state === 'saved') return { label: 'Saved', tone: 'quiet' };
    return null;
}

/**
 * Whether the explicit Save button belongs in the header.
 *
 * It is for the file types that do not save themselves, and only once there is
 * something to save — a button that is present and disabled reads as a feature
 * that is broken rather than a verb that is not needed yet.
 */
export function shouldShowSaveButton({
    previewType,
    canWrite,
    hasUnsavedChanges,
}: {
    previewType: string;
    canWrite: boolean;
    hasUnsavedChanges: boolean;
}): boolean {
    return canWrite && hasUnsavedChanges && !isAutosavedPreviewType(previewType);
}
