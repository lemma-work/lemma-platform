/**
 * Asking the host to put something in the conversation's composer.
 *
 * A widget or an app runs framed, and it holds this whole SDK — so it could
 * create a conversation and stream one itself. It must not. The reply would
 * arrive *inside the frame*, a few hundred pixels of it, while the thread the
 * frame is sitting next to showed nothing at all. The host owns the composer,
 * so the host is asked.
 *
 * And the host fills that composer rather than sending it. Everything in a
 * thread carries a person's name, and a framed view renders what other people
 * wrote — a row out of a table, a page an agent fetched. One keystroke buys the
 * person the edit and means no content can put words in their mouth.
 *
 * So `composeInConversation` is not "send a message". It is "offer one".
 */

export const LEMMA_COMPOSE_MESSAGE_TYPE = 'lemma-compose';
export const LEMMA_COMPOSE_RESULT_MESSAGE_TYPE = 'lemma-compose-result';

/** How long the host has to answer before the offer is treated as unheard. */
const ACKNOWLEDGEMENT_TIMEOUT_MS = 1500;

export interface LemmaComposeOptions {
  /**
   * Offer the text to a new conversation rather than the open one.
   *
   * The default is the conversation the person is looking at, which is what
   * "ask about this" means. Pass this for the other thing — a handoff, where
   * the point is that the subject gets a thread of its own and does not land in
   * the middle of whatever was already being discussed.
   */
  newConversation?: boolean;
}

interface LemmaComposeMessage {
  type: typeof LEMMA_COMPOSE_MESSAGE_TYPE;
  id: string;
  text: string;
  newConversation: boolean;
}

function isAcknowledgement(value: unknown, id: string): boolean {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as { type?: unknown; id?: unknown };
  return candidate.type === LEMMA_COMPOSE_RESULT_MESSAGE_TYPE && candidate.id === id;
}

/**
 * True when there is a host to ask.
 *
 * A page opened on its own — a shared link, an app in its own tab — has no
 * conversation anywhere near it, and a button that quietly does nothing is
 * worse than a button that was never drawn. Check this before rendering one.
 *
 * It answers for the frame, not for the room: a host that does not implement
 * the protocol still looks like a host from in here, which is what the promise
 * from `composeInConversation` is for.
 */
export function canComposeInConversation(): boolean {
  return typeof window !== 'undefined' && window.parent !== window;
}

/**
 * Offer `text` to the conversation's composer.
 *
 * Resolves true once the host says it took it, and false when nothing answered
 * — no host, or a host that does not speak this protocol. Nothing is sent
 * either way: the person reads what arrived in the box and presses enter, or
 * edits it first, or does not.
 */
export function composeInConversation(
  text: string,
  options: LemmaComposeOptions = {},
): Promise<boolean> {
  const body = typeof text === 'string' ? text.trim() : '';
  if (!body || !canComposeInConversation()) return Promise.resolve(false);

  const id = `compose-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const message: LemmaComposeMessage = {
    type: LEMMA_COMPOSE_MESSAGE_TYPE,
    id,
    text: body,
    newConversation: options.newConversation === true,
  };

  return new Promise<boolean>((resolve) => {
    let settled = false;
    const finish = (took: boolean) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      window.removeEventListener('message', hear);
      resolve(took);
    };
    const hear = (event: MessageEvent) => {
      // Only the host answers, and only about this offer. A frame alongside
      // this one is not the host and its replies are not ours.
      if (event.source !== window.parent || !isAcknowledgement(event.data, id)) return;
      finish(true);
    };
    const timer = window.setTimeout(() => finish(false), ACKNOWLEDGEMENT_TIMEOUT_MS);
    window.addEventListener('message', hear);

    try {
      // "*" because the frame does not know its host's origin, and the message
      // carries nothing private — it is text the person is about to be shown
      // and asked to confirm.
      window.parent.postMessage(message, '*');
    } catch {
      finish(false);
    }
  });
}
