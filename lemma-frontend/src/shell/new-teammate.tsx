import { PlusIcon } from "@/ui/icons";
import { NEW_MATE } from "@/copy";

/** The button. Hiring itself is a page — see `stage/hiring.tsx` — because a
 *  candidate deserves the same profile a hired teammate gets, and that does
 *  not fit in a dialog. */

export function NewTeammate({ orgId, onOpen }: { orgId: string | null; onOpen: () => void }) {
    return (
        <>
            <button className="rail__add" aria-label={NEW_MATE} title={NEW_MATE} onClick={onOpen} disabled={!orgId}>
                <span><PlusIcon size={18} /></span>
                <b className="rail__add-label">{NEW_MATE}</b>
            </button>
        </>
    );
}
