"use client";

/**
 * The marks that say where a conversation came from and who wrote it.
 *
 * Deliberately not a skin. The transcript stays one transcript — one type
 * scale, one set of bubbles, the same tool cards and approval cards — because
 * the Lemma conversation is not the platform's conversation and dressing it as
 * WhatsApp would promise a fidelity we cannot deliver. What these add is the
 * information the reader was actually missing: which platform, which place, and
 * which human.
 *
 * Everything here is derived by `lib/assistant/conversation-source.ts`; this
 * file only draws. Styles live in `styles/features/assistant-chat.css`.
 */

import { cn } from "@/lib/utils";
import { ChevronDown, Mail } from "@/components/ui/icons";
import {
  shapeDescription,
  sourceHeadline,
  type ChannelContextEntry,
  type ConversationSource,
  type MessageSender,
} from "@/lib/assistant/conversation-source";

/** The platform's own mark. Email has no logo file and takes an envelope. */
export function SourceGlyph({
  source,
  className,
}: {
  source: ConversationSource;
  className?: string;
}) {
  return (
    <span className={cn("lchat-source-glyph", className)} aria-hidden="true">
      {source.logo ? (
        // Plain <img>: these are static files under /public and an <Image>
        // here would buy nothing but a loader.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={source.logo} alt="" loading="lazy" />
      ) : (
        <Mail className="size-3.5" />
      )}
    </span>
  );
}

/**
 * The conversation's provenance, as a bar above the transcript.
 *
 * Above the scroll area rather than inside it. Sticky inside the transcript is
 * what this was first, and it was wrong twice over: the bar is only as wide as
 * the message column, so bubbles scrolled out from behind it half-drawn, and it
 * sat on top of the first message it was meant to introduce. Outside the
 * scroller it is permanent without ever being in the way.
 *
 * `counterpart` is the other human, and it is absent whenever that human is the
 * reader — which is most of the time, since a conversation holds one member's
 * messages and the copy you are reading is your own.
 */
export function ConversationSourceBanner({
  source,
  counterpart,
}: {
  source: ConversationSource;
  counterpart?: MessageSender | null;
}) {
  return (
    <div className="lchat-source-banner" data-shape={source.shape}>
      <SourceGlyph source={source} />
      <span className="lchat-source-banner-name">{sourceHeadline(source)}</span>
      <span className="lchat-source-banner-part">{shapeDescription(source)}</span>
      {counterpart ? (
        <span className="lchat-source-banner-part">{counterpart.label}</span>
      ) : null}
    </div>
  );
}

/** An email's subject, which a stream of chat bubbles otherwise drops. */
export function MessageSubjectLine({ subject }: { subject: string }) {
  return <div className="lchat-subject">{subject}</div>;
}

/**
 * The channel messages around this one, which the run was given as background
 * and which are not part of this conversation.
 *
 * Collapsed by default and labelled outright, because both alternatives are
 * wrong: hidden, a channel transcript reads as a two-person exchange that never
 * happened; merged in, messages nobody here sent read as part of the thread.
 */
export function ChannelContextNote({
  entries,
  source,
}: {
  entries: ChannelContextEntry[];
  source: ConversationSource;
}) {
  const place = source.channel ?? "the channel";

  // `<details>` rather than a button and a piece of state: a disclosure is
  // exactly what this is, and the native element brings the open/close
  // semantics screen readers already announce.
  return (
    <details className="lchat-context">
      <summary className="lchat-context-toggle">
        <ChevronDown className="lchat-context-caret size-3" />
        <span>
          {entries.length} {entries.length === 1 ? "message" : "messages"} from {place},
          {" "}for context
        </span>
      </summary>

      <ol className="lchat-context-list">
        {entries.map((entry, index) => (
          <li key={`${entry.ts ?? index}-${index}`} className="lchat-context-item">
            <span className="lchat-context-author">{entry.author ?? "Someone"}</span>
            <span className="lchat-context-text">{entry.text}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}
