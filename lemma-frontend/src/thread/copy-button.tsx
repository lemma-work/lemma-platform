"use client";
import { useEffect, useRef, useState } from "react";
import { CheckIcon, CopyIcon } from "@/ui/icons";
import { copyText } from "@/desktop/clipboard";

export function CopyButton({ text, label }: { text: string; label: string }) {
    const [status, setStatus] = useState("");
    const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    useEffect(() => () => clearTimeout(timer.current), []);
    return <span className="copy-control">
        <button type="button" className="copy-control__button" title={status || label} aria-label={status || label} onClick={async () => {
            clearTimeout(timer.current);
            try { await copyText(text); setStatus("Copied"); }
            catch { setStatus("Could not copy. Try again."); }
            timer.current = setTimeout(() => setStatus(""), 2200);
        }}>{status === "Copied" ? <CheckIcon size={15} /> : <CopyIcon size={15} />}</button>
        <span className="sr-only" role="status">{status}</span>
    </span>;
}
