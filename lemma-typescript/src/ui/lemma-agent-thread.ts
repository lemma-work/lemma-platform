import { AgentController, conversationMessageText } from "../core/agent/index.js";
import { resolveUiClient } from "./client.js";
import { BASE_STYLES, HTMLElementBase, escapeHtml } from "./shared.js";

const STYLE = `
${BASE_STYLES}
.wrap {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--lemma-bg);
  border: 1px solid var(--lemma-border);
  border-radius: var(--lemma-radius);
  overflow: hidden;
}
.messages {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: var(--lemma-gap);
}
.msg {
  max-width: 85%;
  padding: 8px 12px;
  border-radius: calc(var(--lemma-radius) - 2px);
  white-space: pre-wrap;
}
.msg.user { align-self: flex-end; background: var(--lemma-accent); color: var(--lemma-accent-fg); }
.msg.assistant { align-self: flex-start; background: var(--lemma-surface); color: var(--lemma-fg); }
.event { align-self: flex-start; color: var(--lemma-muted); font-size: 12px; }
.composer { display: flex; gap: 8px; padding: 12px; border-top: 1px solid var(--lemma-border); }
.composer input {
  flex: 1;
  font: inherit;
  padding: 8px 12px;
  border: 1px solid var(--lemma-border);
  border-radius: calc(var(--lemma-radius) - 4px);
  background: var(--lemma-bg);
  color: var(--lemma-fg);
}
.composer input:focus { outline: 2px solid var(--lemma-accent); outline-offset: -1px; }
`;

/**
 * `<lemma-agent-thread agent="support" conversation-id="...">` — the full chat
 * preset as a no-build custom element. Streams the conversation, renders a
 * message list + composer, and creates the conversation on first send. Theme via
 * CSS vars + ::part(message|composer|input|send).
 */
export class LemmaAgentThreadElement extends HTMLElementBase {
  static get observedAttributes(): string[] {
    return ["agent", "pod", "conversation-id"];
  }

  private controller: AgentController | null = null;
  private unsubscribe: (() => void) | null = null;
  private draft = "";
  private readonly view: ShadowRoot;

  constructor() {
    super();
    this.view = this.attachShadow({ mode: "open" });
  }

  connectedCallback(): void {
    const conversationId = this.getAttribute("conversation-id");
    if (conversationId) {
      const controller = this.ensureController();
      controller.setConversationId(conversationId);
      void controller.refreshConversation(conversationId);
      void controller.loadMessages({ conversationId, limit: 100 });
    }
    this.render();
  }

  disconnectedCallback(): void {
    this.teardown();
  }

  attributeChangedCallback(name: string): void {
    if (name === "agent" || name === "pod") {
      this.teardown();
    }
    if (name === "conversation-id" && this.isConnected) {
      const conversationId = this.getAttribute("conversation-id");
      const controller = this.ensureController();
      controller.setConversationId(conversationId);
      if (conversationId) void controller.loadMessages({ conversationId, limit: 100 });
    }
    if (this.isConnected) this.render();
  }

  /** Send a user message, creating the conversation on first send. */
  async send(text: string): Promise<void> {
    const content = text.trim();
    if (!content) return;
    const controller = this.ensureController();
    if (!controller.getState().conversationId) {
      await controller.createConversation({ setActive: true });
    }
    // A second `sendMessage` mid-run would cancel the stream carrying the
    // answer and open another for the same run, losing everything in between.
    if (controller.getState().isStreaming) {
      await controller.appendMessage(content);
      return;
    }
    await controller.sendMessage(content);
  }

  private ensureController(): AgentController {
    if (!this.controller) {
      this.controller = new AgentController({
        client: resolveUiClient(this.getAttribute("pod")),
        scope: { agentName: this.getAttribute("agent") },
      });
      this.unsubscribe = this.controller.subscribe(() => this.render());
    }
    return this.controller;
  }

  private teardown(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.controller?.destroy();
    this.controller = null;
  }

  private renderMessages(): string {
    const state = this.controller?.getState();
    const rows = (state?.messages ?? [])
      .map((message) => {
        const role = String(message.role || "").toLowerCase();
        const kind = String(message.kind || "");
        if (kind === "TOOL_CALL" || kind === "tool_call" || kind === "TOOL_RETURN" || kind === "tool_return") {
          return `<div part="event" class="event">⚙ ${escapeHtml(String(message.tool_name || "tool"))}</div>`;
        }
        // A settled thought is background for the answer that follows it. The
        // live one still shows, as the activity line below.
        if (kind === "THINKING" || kind === "thinking") return "";
        const text = conversationMessageText(message);
        if (!text) return "";
        const cls = role === "user" ? "user" : "assistant";
        return `<div part="message" class="msg ${cls}" data-role="${cls}">${escapeHtml(text)}</div>`;
      })
      .join("");

    if (state?.isStreaming) {
      const streaming = state.streamingText
        ? `<div part="message" class="msg assistant" data-role="assistant">${escapeHtml(state.streamingText)}</div>`
        : `<div part="activity" class="activity">${state.streamingThinking ? "Thinking…" : "Working…"}</div>`;
      return rows + streaming;
    }
    return rows;
  }

  private render(): void {
    const value = escapeHtml(this.draft);
    this.view.innerHTML = `<style>${STYLE}</style><div part="root" class="wrap">`
      + `<div part="messages" class="messages">${this.renderMessages()}</div>`
      + `<form part="composer" class="composer" data-send>`
      + `<input part="input" data-input type="text" placeholder="Message…" value="${value}" />`
      + `<button part="send" type="submit">Send</button>`
      + `</form></div>`;

    const input = this.view.querySelector<HTMLInputElement>("[data-input]");
    input?.addEventListener("input", () => {
      this.draft = input.value;
    });
    this.view.querySelector("[data-send]")?.addEventListener("submit", (event) => {
      event.preventDefault();
      const text = this.draft;
      this.draft = "";
      void this.send(text);
    });

    const messages = this.view.querySelector(".messages");
    if (messages) messages.scrollTop = messages.scrollHeight;
  }
}
