'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * A real OS window that floats above everything, for watching one thing.
 *
 * The Document Picture-in-Picture API, not the video one: it gives a blank
 * always-on-top window whose document we render into, so a live VNC canvas
 * can go in it rather than only a `<video>`. Chromium-only today, which is
 * why `supported` exists and why every control that offers this has to hide
 * itself where it is absent.
 *
 * The window is its own document, so none of the page's CSS reaches it.
 * Stylesheets are copied across on open -- `<link>` by reference and inline
 * `<style>` by text, because a constructed stylesheet cannot be adopted into
 * a document that did not create it.
 */

interface PictureInPictureApi {
    requestWindow: (options?: {
        width?: number;
        height?: number;
        disallowReturnToOpener?: boolean;
    }) => Promise<Window>;
}

function api(): PictureInPictureApi | null {
    if (typeof window === 'undefined') return null;
    const found = (window as unknown as Record<string, unknown>)
        .documentPictureInPicture;
    return found ? (found as PictureInPictureApi) : null;
}

function copyStyles(into: Window): void {
    for (const sheet of Array.from(document.styleSheets)) {
        const owner = sheet.ownerNode;
        if (!(owner instanceof HTMLElement)) continue;
        if (owner instanceof HTMLLinkElement) {
            // By reference: the PiP window fetches it from the same origin,
            // and cloning the rules out of a cross-origin sheet throws.
            const link = into.document.createElement('link');
            link.rel = 'stylesheet';
            link.href = owner.href;
            into.document.head.append(link);
            continue;
        }
        const style = into.document.createElement('style');
        style.textContent = owner.textContent;
        into.document.head.append(style);
    }
}

export function usePictureInPicture() {
    const [pipWindow, setPipWindow] = useState<Window | null>(null);
    const [supported, setSupported] = useState(false);
    // Read after mount, never during render: the server has no `window`, and
    // deciding this during render is a hydration mismatch waiting to happen.
    useEffect(() => setSupported(api() !== null), []);

    const opening = useRef(false);

    const open = useCallback(async () => {
        const pip = api();
        if (!pip || opening.current) return;
        opening.current = true;
        try {
            const created = await pip.requestWindow({ width: 960, height: 640 });
            copyStyles(created);
            // The document's own background, so the window does not flash
            // white before the canvas paints.
            created.document.body.style.margin = '0';
            created.document.body.style.height = '100vh';
            // `pagehide` rather than `unload`: the person closing the window
            // and the browser reclaiming it both arrive here, and `unload`
            // does not fire reliably in Chromium any more.
            created.addEventListener('pagehide', () => setPipWindow(null), {
                once: true,
            });
            setPipWindow(created);
        } catch {
            // Refused, or no user gesture behind it. Nothing to report: the
            // inline view is still there and is what they were looking at.
        } finally {
            opening.current = false;
        }
    }, []);

    const close = useCallback(() => {
        pipWindow?.close();
        setPipWindow(null);
    }, [pipWindow]);

    // A PiP window outlives the component that opened it, and a floating
    // window showing a pane nobody can reach any more is worse than no
    // window: closing the panel closes it too.
    useEffect(() => () => pipWindow?.close(), [pipWindow]);

    return { supported, pipWindow, open, close };
}
