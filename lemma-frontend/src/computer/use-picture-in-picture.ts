import { useCallback, useEffect, useRef, useState } from "react";

interface PictureInPictureApi {
    requestWindow(options: { width: number; height: number }): Promise<Window>;
}

function api() {
    return (window as Window & { documentPictureInPicture?: PictureInPictureApi }).documentPictureInPicture;
}

export function usePictureInPicture(active: boolean) {
    const [pipWindow, setPipWindow] = useState<Window | null>(null);
    const [supported, setSupported] = useState(false);
    const [failed, setFailed] = useState(false);
    const current = useRef<Window | null>(null);
    const opening = useRef(false);
    const generation = useRef(0);

    const close = useCallback(() => {
        generation.current++;
        current.current?.close();
        current.current = null;
        setPipWindow(null);
    }, []);

    useEffect(() => { setSupported(Boolean(api())); }, []);
    useEffect(() => {
        if (!active) close();
        return () => {
            generation.current++;
            current.current?.close();
            current.current = null;
        };
    }, [active, close]);

    const open = useCallback(async () => {
        const pip = api();
        if (!pip || !active || opening.current || current.current) return;
        opening.current = true;
        const request = generation.current;
        setFailed(false);
        try {
            const created = await pip.requestWindow({ width: 960, height: 640 });
            if (request !== generation.current) { created.close(); return; }
            for (const node of document.querySelectorAll('link[rel="stylesheet"], style')) {
                created.document.head.appendChild(node.cloneNode(true));
            }
            created.document.documentElement.className = document.documentElement.className;
            for (const attribute of document.documentElement.attributes) {
                if (attribute.name.startsWith("data-")) created.document.documentElement.setAttribute(attribute.name, attribute.value);
            }
            created.document.title = "Computer — Lemma";
            created.document.body.className = "computer-popout";
            created.addEventListener("pagehide", () => {
                if (current.current === created) {
                    current.current = null;
                    setPipWindow(null);
                }
            }, { once: true });
            current.current = created;
            setPipWindow(created);
        } catch {
            if (request === generation.current) setFailed(true);
        } finally {
            opening.current = false;
        }
    }, [active]);

    return { supported, pipWindow, failed, open, close };
}
