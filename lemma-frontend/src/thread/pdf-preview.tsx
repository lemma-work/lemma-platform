import { useEffect, useState } from "react";
import { source } from "@/data";

/** The component owns its object URL. Never cache that URL in React Query:
 * a cached URL can outlive the component that revoked it. */
export function PdfPreview({ podId, path, name, size, rawUrl, full }: {
    podId: string; path: string; name: string; size: number; rawUrl?: string; full?: boolean;
}) {
    const [attempt, setAttempt] = useState(0);
    const [preview, setPreview] = useState<{ url?: string; error?: string }>({});
    const large = size > 50 * 1024 * 1024;
    useEffect(() => {
        let cancelled = false;
        let objectUrl: string | undefined;
        setPreview({});
        if (large) { setPreview(rawUrl ? { url: rawUrl } : { error: "This PDF is too large to preview here. Use Download to save it." }); return; }
        void source.downloadFile(podId, path).then(blob => {
            if (cancelled) return;
            objectUrl = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
            setPreview({ url: objectUrl });
        }).catch(() => { if (!cancelled) setPreview({ error: "The PDF could not be loaded. Try again or download it from the toolbar." }); });
        return () => { cancelled = true; if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [podId, path, attempt, large, rawUrl]);
    /* The status row is for when there is something to say. A PDF that loaded
       said "PDF preview" above a visible PDF, next to a card header already
       naming the file and offering to open it — two headers, two opens, and a
       Retry for something that had not failed. A document on screen is its own
       evidence that it loaded; the row is now the loading state and the error,
       which are the two cases a reader actually needs a sentence for. */
    const settled = Boolean(preview.url) && !preview.error;
    return <div className={`pdf-preview${full ? " pdf-preview--full" : ""}`}>
        {!settled && <div className="pdf-preview__status">
            {preview.error ? <span role="alert">{preview.error}</span> : <span>Loading PDF…</span>}
            <div>{preview.url && <a href={preview.url} target="_blank" rel="noreferrer">Open PDF</a>}<button onClick={() => setAttempt(n => n + 1)}>Retry</button></div>
        </div>}
        {preview.url && <object data={preview.url} type="application/pdf" aria-label={name} className="pdf-preview__document"><p>Your browser cannot display this PDF inline. <a href={preview.url} target="_blank" rel="noreferrer">Open PDF</a> or use Download.</p></object>}
    </div>;
}
