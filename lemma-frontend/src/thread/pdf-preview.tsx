import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";

/** The file is cached; the object URL is not. The bytes go in React Query so
 * a card scrolled away and back, or a tab reopened, does not download the
 * whole PDF again. The component still owns its object URL, made from the
 * cached Blob and revoked when it goes: a cached URL can outlive the component
 * that revoked it. */
export function PdfPreview({ podId, path, name, size, rawUrl, full }: {
    podId: string; path: string; name: string; size: number; rawUrl?: string; full?: boolean;
}) {
    const large = size > 50 * 1024 * 1024;
    const file = useQuery({
        queryKey: ["pdf", podId, path],
        queryFn: () => source.downloadFile(podId, path),
        enabled: !large,
        staleTime: 5 * 60_000,
        retry: false,
    });
    const [objectUrl, setObjectUrl] = useState<string | undefined>(undefined);
    useEffect(() => {
        if (!file.data) { setObjectUrl(undefined); return; }
        const made = URL.createObjectURL(new Blob([file.data], { type: "application/pdf" }));
        setObjectUrl(made);
        return () => URL.revokeObjectURL(made);
    }, [file.data]);
    const preview: { url?: string; error?: string } = large
        ? (rawUrl ? { url: rawUrl } : { error: "This PDF is too large to preview here. Use Download to save it." })
        : file.isError
            ? { error: "The PDF could not be loaded. Try again or download it from the toolbar." }
            : { url: objectUrl };
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
            <div>{preview.url && <a href={preview.url} target="_blank" rel="noreferrer">Open PDF</a>}{!large && <button onClick={() => void file.refetch()}>Retry</button>}</div>
        </div>}
        {preview.url && <object data={preview.url} type="application/pdf" aria-label={name} className="pdf-preview__document"><p>Your browser cannot display this PDF inline. <a href={preview.url} target="_blank" rel="noreferrer">Open PDF</a> or use Download.</p></object>}
    </div>;
}
