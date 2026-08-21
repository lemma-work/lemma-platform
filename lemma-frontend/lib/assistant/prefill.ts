/**
 * Opening the assistant with something already written in the composer.
 *
 * A window event rather than a prop or a context value: the things that want to
 * hand the assistant a starting message — an app frame, a resource view — sit
 * anywhere in the tree, and the assistant that receives it is mounted once, in
 * the pod shell, above the router. The event is the seam between them.
 *
 * It fills the draft; it never sends. What to ask for is still the person's
 * sentence to write.
 */

export const ASSISTANT_PREFILL_EVENT = 'lemma-assistant-prefill-draft';

export interface AssistantPrefillDetail {
    content: string;
    forceNewConversation?: boolean;
}

export function requestAssistantPrefill(detail: AssistantPrefillDetail): void {
    if (typeof window === 'undefined') return;
    window.dispatchEvent(new CustomEvent(ASSISTANT_PREFILL_EVENT, { detail }));
}

export function parseAssistantPrefillDetail(
    value: unknown,
): AssistantPrefillDetail | null {
    if (!value || typeof value !== 'object') return null;
    const detail = value as Partial<AssistantPrefillDetail>;
    if (typeof detail.content !== 'string' || detail.content.trim().length === 0) {
        return null;
    }
    return {
        content: detail.content.trim(),
        forceNewConversation: detail.forceNewConversation === true,
    };
}
