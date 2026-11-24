"use client";

import { useState } from "react";
import { ReportIcon } from "@/ui/icons";
import { ReportDialog } from "./report-dialog";
import type { ReportTarget } from "./model";

/** Report, beside Copy on a message: a flag that opens the report dialog.
 *  Shown wherever Copy is -- on hover or keyboard focus, and always on a
 *  touch screen. */
export function ReportButton({ podId, target, author, label }: {
    podId: string;
    target: ReportTarget;
    author: string | null;
    label: string;
}) {
    const [open, setOpen] = useState(false);
    return (
        <>
            <button type="button" className="copy-control__button" title={label} aria-label={label} onClick={() => setOpen(true)}>
                <ReportIcon size={15} />
            </button>
            {open && <ReportDialog podId={podId} target={target} author={author} onClose={() => setOpen(false)} />}
        </>
    );
}
