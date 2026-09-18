'use client';

import { useQuery } from '@tanstack/react-query';
import type { WorkspaceFileListResponse } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const WORKSPACE_ROOT = '/workspace';

export const workspaceFilesQueryKey = (path: string, wake: boolean, after?: string) =>
    ['workspace-files', path, wake, after ?? ''] as const;

/**
 * A directory of the person's own sandbox.
 *
 * `wake` is off by default and that is the point: a paused workspace answers
 * `sleeping: true` rather than being started, so leaving this pane open does not
 * hold compute for as long as it is on screen.
 */
export const useWorkspaceFiles = (path: string, wake: boolean, after?: string) =>
    useQuery<WorkspaceFileListResponse>({
        queryKey: workspaceFilesQueryKey(path, wake, after),
        queryFn: () => getLemmaClient().workspace.listFiles({ path, wake, after }),
        // A sandbox the agent is working in changes under the reader, but not
        // fast enough to be worth polling while nobody is looking at it.
        staleTime: 5_000,
        refetchOnWindowFocus: true,
    });

export const workspaceFileQueryKey = (path: string) =>
    ['workspace-file', path] as const;

/** Bytes we will decode as text before saying "open it another way". */
const MAX_TEXT_BYTES = 1_000_000;

export interface WorkspaceFileContent {
    /** Always present: what a preview renders from, and what a download saves. */
    blob: Blob;
    /** Decoded text, or null when the file is not text, or is too large. */
    text: string | null;
    /** The file is past the text ceiling; nothing was decoded. */
    tooLarge: boolean;
    sizeBytes: number;
}

/**
 * One file's content, already decoded.
 *
 * The decode happens here rather than in the component, because a component
 * that reads a Blob has to do it in an effect, and an effect that sets state is
 * a render the reader sees flash empty first.
 */
export const useWorkspaceFile = (path: string | null, binary: boolean) =>
    useQuery<WorkspaceFileContent>({
        queryKey: [...workspaceFileQueryKey(path ?? ''), binary] as const,
        queryFn: async () => {
            const blob = await getLemmaClient().workspace.readFile(path!);
            // `binary` is the caller saying "do not decode this". Deciding it
            // here from the blob's own type is not an option: the sandbox
            // serves every file as one opaque stream, so an MP4 and a README
            // arrive indistinguishable and only the name says which is which.
            if (binary) {
                return { blob, text: null, tooLarge: false, sizeBytes: blob.size };
            }
            if (blob.size > MAX_TEXT_BYTES) {
                return { blob, text: null, tooLarge: true, sizeBytes: blob.size };
            }
            return {
                blob,
                text: await blob.text(),
                tooLarge: false,
                sizeBytes: blob.size,
            };
        },
        enabled: Boolean(path),
        staleTime: 5_000,
        // Not the app-wide `keepPreviousData`.
        //
        // That default is right for a list, where showing the last page while
        // the next one loads reads as smooth. It is wrong for *this*, because
        // the two halves of the answer come from different places: the blob
        // comes from the query and the filename comes from the selected path.
        // Keeping the previous data means that, for as long as the new file is
        // loading, the pane offers the old file's bytes under the new file's
        // name -- and a download taken in that window saves the wrong file,
        // convincingly. Better to show the pending state for a moment.
        placeholderData: undefined,
    });
