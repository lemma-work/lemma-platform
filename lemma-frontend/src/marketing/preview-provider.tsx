"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";

export function PreviewProvider({ children }: { children: ReactNode }) {
    const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 300_000, refetchOnWindowFocus: false } } }));
    const [standalone, setStandalone] = useState(false);
    useEffect(() => {
        setStandalone(window.parent === window);
        document.documentElement.dataset.theme = "light";
        document.documentElement.dataset.accent = "violet";
        document.documentElement.dataset.corners = "soft";
        function interact(event: Event) {
            if (event.isTrusted && window.parent !== window) window.parent.postMessage({ type: "lemma-tour:interact" }, window.location.origin);
        }
        window.addEventListener("pointerdown", interact, true);
        window.addEventListener("keydown", interact, true);
        return () => {
            window.removeEventListener("pointerdown", interact, true);
            window.removeEventListener("keydown", interact, true);
        };
    }, []);
    return <QueryClientProvider client={client}>
        {standalone ? <div className="preview-document">
            <div className="preview-document__note"><span>Acme sample workspace · No external actions</span><a href="/">Back to Lemma ↗</a></div>
            <div className="preview-document__workspace">{children}</div>
        </div> : children}
    </QueryClientProvider>;
}
