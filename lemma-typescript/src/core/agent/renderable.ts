// Renderable agent-message types. A "renderable message" is the richer,
// parts-based view of a conversation message (text / reasoning / tool parts)
// that the display pipeline operates on. These are pure data types — they live
// in the core so the pipeline (and the React hooks that produce them) share one
// definition. Re-exported from lemma-sdk/react for back-compat.
import type { MessageKind } from "../../types.js";

export interface AssistantToolInvocation {
  toolCallId: string;
  toolName: string;
  args: Record<string, unknown>;
  state: "call" | "result";
  result?: Record<string, unknown>;
}

export type AssistantMessagePart =
  | {
      id: string;
      type: "text";
      text: string;
    }
  | {
      id: string;
      type: "reasoning";
      text: string;
      state?: "streaming" | "done";
      durationMs?: number;
      startedAtMs?: number;
      /** Set when a turn's intermediate text was folded into a collapsible trace. */
      traceNote?: boolean;
    }
  | {
      id: string;
      type: "tool";
      toolInvocation: AssistantToolInvocation;
    };

export interface AssistantRenderableMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  toolInvocations?: AssistantToolInvocation[];
  parts?: AssistantMessagePart[];
  createdAt?: Date;
  conversation_id?: string;
  /**
   * The provisional id this message replaced, when it is the server's echo of a
   * turn that was already on screen. Consumers key turns by identity, and this
   * is what lets the echo inherit the turn the provisional message opened
   * instead of mounting a second one in its place.
   */
  optimistic_id?: string;
  sequence?: number;
  agent_run_id?: string | null;
  /**
   * Which person wrote it. Null on everything an agent produced, and on user
   * messages that predate the column. `role` says a human spoke; this says
   * which one, which is what a conversation with several people in it needs to
   * put a name on a turn.
   */
  sender_user_id?: string | null;
  /**
   * Which agent produced it. Null on anything a person wrote. A conversation
   * can be answered by more than one agent, so an answer that does not say
   * which one is an answer nobody can attribute.
   */
  agent_id?: string | null;
  metadata?: Record<string, unknown> | null;
  message_metadata?: Record<string, unknown> | null;
  /** Flat message fields, passed through so consumers can inspect the raw kind. */
  kind?: MessageKind;
  tool_call_id?: string | null;
  tool_name?: string | null;
  tool_args?: unknown;
  tool_result?: unknown;
}
