'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { toast } from 'sonner';

import {
    APP_EDITOR_MESSAGE,
    buildAppEditorHelloMessage,
    buildAppEditorSelectModeMessage,
    describeAppEditorSelection,
    buildAppEditorPrefill,
    parseAppEditorMessage,
} from '@/lib/app/app-editor';
import { requestAssistantPrefill } from '@/lib/assistant/prefill';

/**
 * Drives the element picker inside a running app frame.
 *
 * The app answers `hello` only if it carries the injected bridge, so `ready` is
 * what the toggle waits on rather than an assumption that every app can be
 * picked from. Picking one element opens the assistant with the element already
 * described; the sentence about what to change is still the person's to write.
 *
 * Readiness is tracked as *which load* answered rather than as a boolean that
 * something has to remember to clear. A reload replaces the document and the
 * bridge inside it, so a stale yes would leave a toggle that talks to a window
 * no longer listening.
 */
export function useAppEditor({
    iframeRef,
    url,
    appName,
    enabled,
    frameLoaded,
}: {
    iframeRef: React.RefObject<HTMLIFrameElement | null>;
    url: string;
    appName: string;
    enabled: boolean;
    frameLoaded: boolean;
}) {
    const [readyLoadKey, setReadyLoadKey] = useState<string | null>(null);
    const [selectRequested, setSelectRequested] = useState(false);
    const selectRequestedRef = useRef(false);

    const loadKey = useMemo(
        () => `${url}#${frameLoaded ? 'loaded' : 'pending'}`,
        [frameLoaded, url],
    );

    const appOrigin = useMemo(() => {
        if (typeof window === 'undefined') return null;
        try {
            return new URL(url, window.location.href).origin;
        } catch {
            return null;
        }
    }, [url]);

    const post = useCallback(
        (message: object) => {
            const frame = iframeRef.current;
            if (!frame?.contentWindow || !appOrigin) return;
            frame.contentWindow.postMessage(message, appOrigin);
        },
        [appOrigin, iframeRef],
    );

    const ready = enabled && frameLoaded && readyLoadKey === loadKey;
    const selecting = ready && selectRequested;

    /** Follow the app's own view of select mode, without echoing it back. */
    const trackSelectMode = useCallback((active: boolean) => {
        selectRequestedRef.current = active;
        setSelectRequested(active);
    }, []);

    const requestSelectMode = useCallback(
        (active: boolean) => {
            trackSelectMode(active);
            post(buildAppEditorSelectModeMessage(active));
        },
        [post, trackSelectMode],
    );

    useEffect(() => {
        if (!enabled || !frameLoaded) return;
        post(buildAppEditorHelloMessage());
    }, [enabled, frameLoaded, loadKey, post]);

    useEffect(() => {
        if (!enabled || !appOrigin) return;

        const onMessage = (event: MessageEvent) => {
            // Both checks matter: the origin alone would accept any frame served
            // from the app's host, and the window alone would accept whatever
            // that window later navigated to.
            if (event.origin !== appOrigin) return;
            if (event.source !== iframeRef.current?.contentWindow) return;

            const message = parseAppEditorMessage(event.data);
            if (!message) return;

            if (message.type === APP_EDITOR_MESSAGE.ready) {
                setReadyLoadKey(loadKey);
                // A fresh document is not in select mode, whatever the last one was.
                trackSelectMode(false);
                return;
            }
            if (message.type === APP_EDITOR_MESSAGE.selectMode) {
                // The app leaves select mode itself on Escape and after a pick,
                // so the toggle follows the app rather than the reverse.
                trackSelectMode(message.active);
                return;
            }
            if (message.type === APP_EDITOR_MESSAGE.selection) {
                trackSelectMode(false);
                requestAssistantPrefill({
                    content: buildAppEditorPrefill(message.selection, appName),
                });
                toast.success(`Selected ${describeAppEditorSelection(message.selection)}`);
            }
        };

        window.addEventListener('message', onMessage);
        return () => window.removeEventListener('message', onMessage);
    }, [appName, appOrigin, enabled, iframeRef, loadKey, trackSelectMode]);

    // The app cancels on Escape too, but only while it holds focus — and after
    // the toggle is clicked, focus is still on the toggle. Without this, the one
    // way out of select mode would be to pick something.
    useEffect(() => {
        if (!selecting) return;

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key !== 'Escape') return;
            event.preventDefault();
            requestSelectMode(false);
        };

        window.addEventListener('keydown', onKeyDown);
        return () => window.removeEventListener('keydown', onKeyDown);
    }, [requestSelectMode, selecting]);

    const toggleSelecting = useCallback(() => {
        requestSelectMode(!selectRequestedRef.current);
    }, [requestSelectMode]);

    return { ready, selecting, toggleSelecting };
}
