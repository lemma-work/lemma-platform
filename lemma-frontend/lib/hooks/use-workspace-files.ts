'use client';

import { useQuery } from '@tanstack/react-query';
import type { WorkspaceFileListResponse } from 'lemma-sdk';

import { getLemmaClient } from '@/lib/sdk/lemma-client';

export const WORKSPACE_ROOT = '/workspace';

/**
 * Where one conversation's own files live.
 *
 * Mirrors `BaseAgentContext.get_workspace_cwd()`. The sandbox is one machine per
 * person, so this is the only thing that separates one conversation's work from
 * another's — there is no second sandbox to put it in.
 */
export const conversationDirectory = (conversationId: string): string =>
    `${WORKSPACE_ROOT}/conversations/${conversationId}`;

export const workspaceFilesQueryKey = (path: string, wake: boolean) =>
    ['workspace-files', path, wake] as const;

/**
 * A directory of the person's own sandbox.
 *
 * `wake` is off by default and that is the point: a paused workspace answers
 * `sleeping: true` rather than being started, so leaving this pane open does not
 * hold compute for as long as it is on screen.
 */
export const useWorkspaceFiles = (path: string, wake: boolean) =>
    useQuery<WorkspaceFileListResponse>({
        queryKey: workspaceFilesQueryKey(path, wake),
        queryFn: () => getLemmaClient().workspace.listFiles({ path, wake }),
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
    /** Present for an image, so the viewer can show it. */
    blob: Blob;
    /** Decoded text, or null when the file is an image or too large. */
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
export const useWorkspaceFile = (path: string | null, isImage: boolean) =>
    useQuery<WorkspaceFileContent>({
        queryKey: [...workspaceFileQueryKey(path ?? ''), isImage] as const,
        queryFn: async () => {
            const blob = await getLemmaClient().workspace.readFile(path!);
            if (isImage) {
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
    });
