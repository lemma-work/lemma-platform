/**
 * What the composer's one primary button is, right now.
 *
 * Extracted from the component because it is the whole of the decision and the
 * component around it cannot be rendered in this package's test setup (vitest
 * runs `environment: 'node'` here, with no DOM stack). Two regressions came out
 * of this expression being written inline and read by eye:
 *
 * - Dropping `isBusy` from `canSend` to let a running conversation take a
 *   follow-up also un-disabled Send on pod home, which passes no `onStop` and
 *   whose submit handler returns early while busy — an enabled button that did
 *   nothing.
 * - On the assistant the same change was inert, because a Stop button replaced
 *   Send whenever anything was running. The only way to send a follow-up was
 *   the Enter key, and the one visible button stopped the run instead.
 *
 * So a surface has to *say* that it takes a send while busy, and saying so is
 * what moves Stop out of the way once there is something to send.
 */
export interface ComposerActionInput {
    hasDraft: boolean;
    hasAttachments: boolean;
    /** No write access, or an interaction card is waiting on the person. */
    disabled: boolean;
    /** Something is running: a run, an upload, a route handoff. */
    isBusy: boolean;
    /** This surface can send a follow-up into whatever is already running. */
    busyAcceptsSend: boolean;
    /** An `onStop` handler was given, so Stop is an option at all. */
    canStop: boolean;
}

export interface ComposerActionState {
    canSend: boolean;
    showStop: boolean;
}

export function composerActionState({
    hasDraft,
    hasAttachments,
    disabled,
    isBusy,
    busyAcceptsSend,
    canStop,
}: ComposerActionInput): ComposerActionState {
    const canSend = (hasDraft || hasAttachments)
        && !disabled
        && (!isBusy || busyAcceptsSend);
    // Send wins over Stop when there is something to send. Stop is one keystroke
    // away — clear the box — whereas a Send the person cannot reach at all is
    // the thing they typed a sentence for.
    return { canSend, showStop: isBusy && canStop && !canSend };
}
