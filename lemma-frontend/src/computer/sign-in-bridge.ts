/** A sign-in card asking the shell to open the sign-in beside the thread.
 *
 *  The card sits deep in a transcript and the stage is the shell's, so it asks
 *  through a DOM event rather than a prop threaded through every level. The
 *  event is cancelable and the shell cancels it to say "mine": a card on a
 *  page with no shell (the standalone sign-in link) gets `false` back and
 *  opens its own dialog, as it always did. */
export const SIGN_IN_EVENT = "lemma:sign-in";

export interface SignInRequest {
    conversationId: string;
    toolCallId: string;
    host: string;
}

export function requestSignIn(request: SignInRequest): boolean {
    const event = new CustomEvent<SignInRequest>(SIGN_IN_EVENT, { detail: request, cancelable: true });
    return !window.dispatchEvent(event);
}
