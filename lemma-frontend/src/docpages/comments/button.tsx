"use client";

import { ChatIcon } from "@/ui/icons";
import { toggleComments, useCommentsEntry } from "./toggle";

/** Comment, beside Share: the page's threads, and how many are open. */
export function CommentsButton({ path }: { path: string }) {
    const { open, count } = useCommentsEntry(path);
    return (
        <button className="comments-pill" aria-pressed={open} onClick={() => toggleComments(path)} title={open ? "Hide comments" : "Comments"}>
            <ChatIcon size={15} />
            {count > 0 ? count : <span className="comments-pill__label">Comment</span>}
        </button>
    );
}
