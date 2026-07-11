"use strict";
var LemmaUI = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);
  var __publicField = (obj, key, value) => __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);

  // src/browser-ui.ts
  var browser_ui_exports = {};
  __export(browser_ui_exports, {
    LemmaAgentTaskElement: () => LemmaAgentTaskElement,
    LemmaAgentThreadElement: () => LemmaAgentThreadElement,
    defineLemmaElements: () => defineLemmaElements
  });

  // src/core/agent/messages.ts
  function isRecord(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }
  function normalizeConversationStatus(status) {
    if (typeof status !== "string") return void 0;
    const normalized = status.trim().toUpperCase();
    return normalized.length > 0 ? normalized : void 0;
  }
  function isConversationRunningStatus(status) {
    const normalized = normalizeConversationStatus(status);
    if (!normalized) return false;
    return normalized === "RUNNING" || normalized === "IN_PROGRESS" || normalized === "PROCESSING";
  }
  function extractTextFromStructuredContentEntry(entry) {
    if (typeof entry === "string") return entry.trim();
    if (!isRecord(entry)) return "";
    if (typeof entry.text === "string") return entry.text.trim();
    if (typeof entry.content === "string") return entry.content.trim();
    if (typeof entry.value === "string") return entry.value.trim();
    if (Array.isArray(entry.content)) {
      const nested = entry.content.map((child) => extractTextFromStructuredContentEntry(child)).filter((text) => text.length > 0).join("\n").trim();
      if (nested.length > 0) return nested;
    }
    if (Array.isArray(entry.summary)) {
      const summary = entry.summary.map((child) => extractTextFromStructuredContentEntry(child)).filter((text) => text.length > 0).join("\n").trim();
      if (summary.length > 0) return summary;
    }
    return "";
  }
  function extractConversationMessageText(content) {
    if (typeof content === "string") return content.trim();
    if (Array.isArray(content)) {
      return content.map((entry) => extractTextFromStructuredContentEntry(entry)).filter((text) => text.length > 0).join("\n\n").trim();
    }
    if (!isRecord(content)) return "";
    const directContent = content.content;
    if (typeof directContent === "string") return directContent.trim();
    if (Array.isArray(directContent)) {
      const text = directContent.map((entry) => extractTextFromStructuredContentEntry(entry)).filter((entry) => entry.length > 0).join("\n\n").trim();
      if (text.length > 0) return text;
    }
    if (typeof content.text === "string") return content.text.trim();
    return extractTextFromStructuredContentEntry(content);
  }
  function conversationMessageText(message) {
    if (!message) return "";
    if (typeof message.text === "string" && message.text.trim().length > 0) {
      return message.text.trim();
    }
    if (message.tool_result !== void 0 && message.tool_result !== null) {
      return extractConversationMessageText(message.tool_result);
    }
    return "";
  }
  function getLatestAssistantMessage(messages) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (typeof (message == null ? void 0 : message.role) === "string" && message.role.toLowerCase() === "assistant") {
        return message;
      }
    }
    return null;
  }

  // src/streams.ts
  async function* readSSE(stream) {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let eventName;
    let dataLines = [];
    const flush = () => {
      if (dataLines.length === 0) {
        eventName = void 0;
        return null;
      }
      const data = dataLines.join("\n");
      const raw = `${eventName ? `event: ${eventName}
` : ""}${dataLines.map((line) => `data: ${line}`).join("\n")}`;
      const next = {
        event: eventName,
        data,
        raw
      };
      eventName = void 0;
      dataLines = [];
      return next;
    };
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      for (const line of lines) {
        const trimmed = line.endsWith("\r") ? line.slice(0, -1) : line;
        if (trimmed === "") {
          const event2 = flush();
          if (event2) {
            yield event2;
          }
          continue;
        }
        if (trimmed.startsWith("event:")) {
          eventName = trimmed.slice("event:".length).trim();
          continue;
        }
        if (trimmed.startsWith("data:")) {
          dataLines.push(trimmed.slice("data:".length).trim());
        }
      }
    }
    const event = flush();
    if (event) {
      yield event;
    }
  }
  function parseSSEJson(event) {
    if (!event.data || event.data === "[DONE]") {
      return null;
    }
    try {
      return JSON.parse(event.data);
    } catch {
      return null;
    }
  }

  // src/assistant-events.ts
  var CONVERSATION_STATUS_ALIASES = {
    RUNNING: "RUNNING",
    IN_PROGRESS: "IN_PROGRESS",
    PROCESSING: "PROCESSING",
    STOP_REQUESTED: "STOP_REQUESTED",
    WAITING: "WAITING",
    COMPLETED: "COMPLETED",
    COMPLETE: "COMPLETED",
    DONE: "COMPLETED",
    FAILED: "FAILED",
    ERROR: "FAILED",
    STOPPED: "STOPPED",
    CANCELLED: "STOPPED",
    CANCELED: "STOPPED"
  };
  function isRecord2(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }
  function normalizeStatus(status) {
    if (typeof status !== "string") return void 0;
    const normalized = status.trim().toUpperCase().replace(/[-\s]+/g, "_");
    return CONVERSATION_STATUS_ALIASES[normalized];
  }
  function toConversationMessage(value) {
    if (!isRecord2(value)) return void 0;
    if (typeof value.id !== "string") return void 0;
    if (typeof value.role !== "string") return void 0;
    if (typeof value.kind !== "string") return void 0;
    const message = {
      id: value.id,
      role: value.role,
      kind: value.kind,
      text: typeof value.text === "string" ? value.text : null,
      tool_name: typeof value.tool_name === "string" ? value.tool_name : null,
      tool_call_id: typeof value.tool_call_id === "string" ? value.tool_call_id : null,
      tool_args: "tool_args" in value ? value.tool_args : null,
      tool_result: "tool_result" in value ? value.tool_result : null,
      created_at: typeof value.created_at === "string" ? value.created_at : (/* @__PURE__ */ new Date()).toISOString(),
      conversation_id: typeof value.conversation_id === "string" ? value.conversation_id : void 0,
      sequence: typeof value.sequence === "number" ? value.sequence : void 0,
      agent_run_id: typeof value.agent_run_id === "string" ? value.agent_run_id : null,
      metadata: isRecord2(value.metadata) ? value.metadata : null
    };
    return message;
  }
  function extractPayload(record) {
    if ("data" in record) return record.data;
    if ("payload" in record) return record.payload;
    return void 0;
  }
  function extractStatus(payload) {
    var _a, _b, _c;
    if (isRecord2(payload)) {
      return (_c = (_b = (_a = normalizeStatus(payload.status)) != null ? _a : normalizeStatus(payload.conversation_status)) != null ? _b : normalizeStatus(payload.run_status)) != null ? _c : isRecord2(payload.conversation) ? normalizeStatus(payload.conversation.status) : void 0;
    }
    return normalizeStatus(payload);
  }
  function extractErrorMessage(payload) {
    if (typeof payload === "string") {
      const message = payload.trim();
      return message.length > 0 ? message : void 0;
    }
    if (isRecord2(payload)) {
      const message = [
        payload.message,
        payload.error,
        payload.detail,
        payload.reason,
        payload.description
      ].find((value) => typeof value === "string" && value.trim().length > 0);
      if (typeof message === "string") {
        return message.trim();
      }
    }
    return void 0;
  }
  function parseAssistantStreamEvent(value) {
    var _a, _b;
    const directMessage = toConversationMessage(value);
    if (directMessage) {
      return { message: directMessage };
    }
    if (!isRecord2(value)) {
      return {};
    }
    const eventType = typeof value.type === "string" ? value.type.toLowerCase() : "";
    const payload = extractPayload(value);
    if (eventType === "token" && typeof payload === "string") {
      const tokenKind = typeof value.kind === "string" && value.kind.trim() ? value.kind.trim().toLowerCase() : "text";
      return { token: payload, tokenKind };
    }
    if (eventType === "message" || eventType === "message_added") {
      const message = toConversationMessage(payload);
      return message ? { message } : {};
    }
    if (eventType === "status" || eventType === "conversation_status" || eventType === "conversation_updated" || eventType === "run_status") {
      const status = extractStatus(payload);
      return status ? { status } : {};
    }
    if (eventType === "completed") {
      const conversationStatus = isRecord2(payload) ? normalizeStatus(payload.conversation_status) : void 0;
      const status = (_a = conversationStatus != null ? conversationStatus : extractStatus(payload)) != null ? _a : "COMPLETED";
      return { status };
    }
    if (eventType === "error" || eventType === "stream_error") {
      return {
        status: "FAILED",
        error: (_b = extractErrorMessage(payload)) != null ? _b : "Agent run failed."
      };
    }
    return {};
  }
  function upsertConversationMessage(messages, incoming) {
    const next = [...messages];
    const index = next.findIndex((message) => message.id === incoming.id);
    if (index >= 0) {
      next[index] = incoming;
    } else {
      next.push(incoming);
    }
    next.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    return next;
  }

  // src/core/agent/agent-controller.ts
  function normalizeError(error, fallback) {
    if (error instanceof Error) return error;
    if (typeof error === "string" && error.trim()) return new Error(error.trim());
    return new Error(fallback);
  }
  function applyPodScope(client, podId) {
    const resolvedPodId = podId != null ? podId : client.podId;
    if (resolvedPodId && resolvedPodId !== client.podId) {
      return client.withPod(resolvedPodId);
    }
    return client;
  }
  function requireConversationId(conversationId) {
    if (!conversationId) {
      throw new Error("conversationId is required.");
    }
    return conversationId;
  }
  function normalizeScope(client, defaults, override) {
    var _a, _b, _c, _d, _e, _f, _g, _h, _i, _j, _k, _l, _m, _n, _o;
    const resolvedAgentName = (_f = (_e = (_d = (_c = (_b = (_a = override == null ? void 0 : override.agentName) != null ? _a : override == null ? void 0 : override.assistantName) != null ? _b : override == null ? void 0 : override.assistantId) != null ? _c : defaults.agentName) != null ? _d : defaults.assistantName) != null ? _e : defaults.assistantId) != null ? _f : null;
    return {
      podId: (_i = (_h = (_g = override == null ? void 0 : override.podId) != null ? _g : defaults.podId) != null ? _h : client.podId) != null ? _i : null,
      agentName: resolvedAgentName,
      assistantName: (_k = (_j = override == null ? void 0 : override.assistantName) != null ? _j : defaults.assistantName) != null ? _k : null,
      assistantId: (_m = (_l = override == null ? void 0 : override.assistantId) != null ? _l : defaults.assistantId) != null ? _m : null,
      organizationId: (_o = (_n = override == null ? void 0 : override.organizationId) != null ? _n : defaults.organizationId) != null ? _o : null
    };
  }
  function isRecord3(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }
  function parseMaybeJsonValue(value) {
    if (typeof value !== "string") return value;
    const trimmed = value.trim();
    if (!trimmed) return value;
    try {
      return JSON.parse(trimmed);
    } catch {
      return value;
    }
  }
  function parseMaybeJsonObject(value) {
    const parsed = parseMaybeJsonValue(value);
    return isRecord3(parsed) ? parsed : {};
  }
  function normalizeToolResult(value) {
    const parsed = parseMaybeJsonValue(value);
    if (isRecord3(parsed)) return parsed;
    if (Array.isArray(parsed)) return { output: parsed };
    if (typeof parsed === "undefined" || parsed === null) return {};
    return { output: parsed };
  }
  function parseStreamingToolToken(token) {
    var _a, _b, _c, _d, _e, _f, _g;
    const parsed = parseMaybeJsonValue(token);
    if (!isRecord3(parsed)) return null;
    const toolName = [parsed.tool_name, parsed.toolName, parsed.name].find((value) => typeof value === "string" && value.trim().length > 0);
    if (typeof toolName !== "string") return null;
    const rawToolCallId = [parsed.tool_call_id, parsed.toolCallId, parsed.call_id, parsed.id].find((value) => typeof value === "string" && value.trim().length > 0);
    const rawArgs = (_d = (_c = (_b = (_a = parsed.tool_args) != null ? _a : parsed.tool_input) != null ? _b : parsed.args) != null ? _c : parsed.arguments) != null ? _d : parsed.input;
    const rawResult = (_g = (_f = (_e = parsed.tool_result) != null ? _e : parsed.tool_output) != null ? _f : parsed.result) != null ? _g : parsed.output;
    const hasResult = typeof rawResult !== "undefined";
    return {
      ...typeof rawToolCallId === "string" ? { toolCallId: rawToolCallId } : {},
      toolName,
      args: parseMaybeJsonObject(rawArgs),
      state: hasResult ? "result" : "call",
      ...hasResult ? { result: normalizeToolResult(rawResult) } : {}
    };
  }
  function parsePartialStreamingToolToken(token) {
    const toolNameMatch = /"(?:tool_name|toolName|name)"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(token);
    if (!(toolNameMatch == null ? void 0 : toolNameMatch[1])) return null;
    const idMatch = /"(?:tool_call_id|toolCallId|call_id|id)"\s*:\s*"((?:\\.|[^"\\])*)"/.exec(token);
    const unescapeJsonString = (value) => {
      try {
        return JSON.parse(`"${value}"`);
      } catch {
        return value;
      }
    };
    return {
      ...(idMatch == null ? void 0 : idMatch[1]) ? { toolCallId: unescapeJsonString(idMatch[1]) } : {},
      toolName: unescapeJsonString(toolNameMatch[1]),
      args: {},
      state: "call"
    };
  }
  function resolveResumeInput(input) {
    if (typeof input === "string" || input === null) {
      return { conversationId: input };
    }
    return input != null ? input : {};
  }
  function selectAgentOutputs(state) {
    const latestAssistantMessage = getLatestAssistantMessage(state.messages);
    const output = latestAssistantMessage != null ? latestAssistantMessage : null;
    const latestAssistantText = conversationMessageText(latestAssistantMessage);
    const settled = !state.isStreaming && !isConversationRunningStatus(state.status);
    return {
      latestAssistantMessage,
      output,
      outputText: state.streamingText.trim() || latestAssistantText,
      finalOutput: settled ? output : null,
      finalOutputText: settled ? latestAssistantText : ""
    };
  }
  var AgentController = class {
    constructor(options) {
      __publicField(this, "options");
      __publicField(this, "state");
      __publicField(this, "listeners", /* @__PURE__ */ new Set());
      __publicField(this, "abort", null);
      __publicField(this, "streamingBuffer", "");
      __publicField(this, "streamingToolToken", "");
      __publicField(this, "autoResumedKey", null);
      __publicField(this, "streamReconnectCount", 0);
      __publicField(this, "pendingFlush", null);
      // -- store ------------------------------------------------------------------
      __publicField(this, "subscribe", (listener) => {
        this.listeners.add(listener);
        return () => {
          this.listeners.delete(listener);
        };
      });
      /** Stable snapshot for `useSyncExternalStore`: identity changes only on patch. */
      __publicField(this, "getState", () => this.state);
      /** Replace the live options (client/scope/callbacks); does not emit. */
      __publicField(this, "setOptions", (options) => {
        this.options = options;
      });
      // -- lifecycle --------------------------------------------------------------
      __publicField(this, "cancel", () => {
        var _a;
        (_a = this.abort) == null ? void 0 : _a.abort();
        this.abort = null;
      });
      /** Abort any active stream and clear timers. Call when the owner unmounts. */
      __publicField(this, "destroy", () => {
        var _a;
        (_a = this.abort) == null ? void 0 : _a.abort();
        this.abort = null;
        if (this.pendingFlush) {
          clearTimeout(this.pendingFlush);
          this.pendingFlush = null;
        }
        this.listeners.clear();
      });
      __publicField(this, "setConversationId", (nextConversationId) => {
        var _a;
        (_a = this.abort) == null ? void 0 : _a.abort();
        this.abort = null;
        if (this.state.conversationId === nextConversationId) {
          return;
        }
        this.autoResumedKey = null;
        this.streamingBuffer = "";
        this.streamingToolToken = "";
        this.streamReconnectCount = 0;
        if (this.pendingFlush) {
          clearTimeout(this.pendingFlush);
          this.pendingFlush = null;
        }
        this.patch({
          conversationId: nextConversationId,
          conversation: null,
          status: void 0,
          messages: [],
          streamingText: "",
          streamingTool: null,
          isStreaming: false,
          error: null
        });
      });
      __publicField(this, "clearMessages", () => {
        this.patch({ messages: [] });
      });
      // -- reads ------------------------------------------------------------------
      __publicField(this, "listConversations", async (input = {}) => {
        var _a, _b, _c, _d, _e, _f, _g, _h;
        this.patch({ error: null });
        try {
          const scope = normalizeScope(this.client, this.scopeDefaults, input.scope);
          const scopedClient = applyPodScope(this.client, scope.podId);
          const response = await scopedClient.conversations.list({
            pod_id: (_a = scope.podId) != null ? _a : void 0,
            agent_name: (_b = scope.agentName) != null ? _b : void 0,
            limit: input.limit,
            page_token: input.pageToken
          });
          return {
            items: (_c = response.items) != null ? _c : [],
            limit: (_e = (_d = response.limit) != null ? _d : input.limit) != null ? _e : 20,
            next_page_token: response.next_page_token,
            total: response.total
          };
        } catch (listError) {
          const normalized = normalizeError(listError, "Failed to list conversations.");
          this.patch({ error: normalized });
          (_g = (_f = this.options).onError) == null ? void 0 : _g.call(_f, listError);
          return { items: [], limit: (_h = input.limit) != null ? _h : 20, next_page_token: null };
        }
      });
      __publicField(this, "createConversation", async (input = {}) => {
        var _a, _b, _c, _d, _e, _f, _g, _h, _i, _j, _k, _l, _m, _n, _o, _p, _q, _r;
        this.patch({ error: null });
        try {
          const scopedClient = applyPodScope(this.client, (_b = (_a = input.podId) != null ? _a : this.scopeDefaults.podId) != null ? _b : null);
          const payload = {
            title: (_c = input.title) != null ? _c : void 0,
            instructions: typeof input.instructions === "undefined" ? (_d = this.options.instructions) != null ? _d : void 0 : input.instructions,
            metadata: (_e = input.metadata) != null ? _e : void 0,
            pod_id: (_h = (_g = (_f = input.podId) != null ? _f : this.scopeDefaults.podId) != null ? _g : scopedClient.podId) != null ? _h : void 0,
            agent_name: (_n = (_m = (_l = (_k = (_j = (_i = input.agentName) != null ? _i : input.assistantName) != null ? _j : input.assistantId) != null ? _k : this.scopeDefaults.agentName) != null ? _l : this.scopeDefaults.assistantName) != null ? _m : this.scopeDefaults.assistantId) != null ? _n : void 0,
            model: typeof input.model === "undefined" ? void 0 : input.model,
            agent_runtime: typeof input.agentRuntime === "undefined" ? void 0 : input.agentRuntime,
            parent_id: (_o = input.parentId) != null ? _o : void 0
          };
          const created = await scopedClient.conversations.create(payload);
          if (input.setActive !== false) {
            this.autoResumedKey = null;
            this.streamingBuffer = "";
            if (this.pendingFlush) {
              clearTimeout(this.pendingFlush);
              this.pendingFlush = null;
            }
            this.patch({
              conversationId: created.id,
              conversation: created,
              messages: [],
              streamingText: ""
            });
            this.setConversationStatus((_p = created.status) != null ? _p : void 0);
          }
          return created;
        } catch (createError) {
          const normalized = normalizeError(createError, "Failed to create conversation.");
          this.patch({ error: normalized });
          (_r = (_q = this.options).onError) == null ? void 0 : _r.call(_q, createError);
          throw normalized;
        }
      });
      __publicField(this, "refreshConversation", async (explicitConversationId) => {
        var _a, _b, _c;
        const id = explicitConversationId != null ? explicitConversationId : this.state.conversationId;
        if (!id) return null;
        this.patch({ error: null });
        try {
          const scope = normalizeScope(this.client, this.scopeDefaults);
          const scopedClient = applyPodScope(this.client, scope.podId);
          const nextConversation = await scopedClient.conversations.get(id, {
            pod_id: (_a = scope.podId) != null ? _a : void 0
          });
          this.patch({ conversation: nextConversation });
          this.setConversationStatus(
            typeof nextConversation.status === "string" ? nextConversation.status : void 0
          );
          return nextConversation;
        } catch (refreshError) {
          const normalized = normalizeError(refreshError, "Failed to fetch conversation.");
          this.patch({ error: normalized });
          (_c = (_b = this.options).onError) == null ? void 0 : _c.call(_b, refreshError);
          return null;
        }
      });
      __publicField(this, "loadMessages", async (input = {}) => {
        var _a, _b, _c, _d, _e, _f, _g, _h, _i, _j;
        const id = (_a = input.conversationId) != null ? _a : this.state.conversationId;
        if (!id) {
          return { items: [], limit: (_b = input.limit) != null ? _b : 20, next_page_token: null };
        }
        this.patch({ error: null });
        try {
          const response = await this.client.conversations.messages.list(id, {
            limit: input.limit,
            page_token: input.pageToken
          });
          const nextMessages = (_c = response.items) != null ? _c : [];
          if (this.state.conversationId !== id) {
            return {
              items: nextMessages,
              limit: (_e = (_d = response.limit) != null ? _d : input.limit) != null ? _e : 20,
              next_page_token: response.next_page_token
            };
          }
          this.patch({
            messages: nextMessages.reduce(
              (accumulator, message) => upsertConversationMessage(accumulator, message),
              this.state.messages
            )
          });
          return {
            items: nextMessages,
            limit: (_g = (_f = response.limit) != null ? _f : input.limit) != null ? _g : 20,
            next_page_token: response.next_page_token
          };
        } catch (messageError) {
          const normalized = normalizeError(messageError, "Failed to fetch conversation messages.");
          this.patch({ error: normalized });
          (_i = (_h = this.options).onError) == null ? void 0 : _i.call(_h, messageError);
          return { items: [], limit: (_j = input.limit) != null ? _j : 20, next_page_token: null };
        }
      });
      // -- streaming --------------------------------------------------------------
      __publicField(this, "consume", async ({
        stream,
        controller,
        streamConversationId,
        syncAfterStream
      }) => {
        var _a, _b, _c, _d, _e, _f, _g, _h, _i, _j;
        this.patch({ isStreaming: true, error: null });
        this.clearStreamingText();
        let sawTerminalStatus = false;
        try {
          for await (const event of readSSE(stream)) {
            if (controller.signal.aborted) break;
            const payload = parseSSEJson(event);
            (_b = (_a = this.options).onEvent) == null ? void 0 : _b.call(_a, event, payload);
            const parsed = parseAssistantStreamEvent(payload);
            if (parsed.error) {
              const streamError = new Error(parsed.error);
              this.patch({ error: streamError });
              (_d = (_c = this.options).onError) == null ? void 0 : _d.call(_c, streamError);
              this.setConversationStatus((_e = parsed.status) != null ? _e : "FAILED");
              sawTerminalStatus = true;
              this.clearStreamingText();
              this.clearStreamingTool();
              continue;
            }
            if (parsed.token) {
              if (parsed.tokenKind === "tool") {
                this.streamingToolToken += parsed.token;
                const tool = parseStreamingToolToken(this.streamingToolToken) || parsePartialStreamingToolToken(this.streamingToolToken);
                if ((tool == null ? void 0 : tool.state) === "call") {
                  this.patch({ streamingTool: tool });
                  if (parseStreamingToolToken(this.streamingToolToken)) {
                    this.streamingToolToken = "";
                  }
                } else if ((tool == null ? void 0 : tool.state) === "result") {
                  const current = this.state.streamingTool;
                  this.patch({
                    streamingTool: (current == null ? void 0 : current.toolCallId) && current.toolCallId === tool.toolCallId ? { ...current, ...tool } : current
                  });
                  this.streamingToolToken = "";
                }
              } else if (!parsed.tokenKind || parsed.tokenKind === "text") {
                this.appendStreamingToken(parsed.token);
              }
            }
            if (parsed.message) {
              this.patch({ messages: upsertConversationMessage(this.state.messages, parsed.message) });
              (_g = (_f = this.options).onMessage) == null ? void 0 : _g.call(_f, parsed.message);
              const role = typeof parsed.message.role === "string" ? parsed.message.role.toLowerCase() : "";
              if (role === "assistant" || role === "tool") {
                this.clearStreamingText();
                this.clearStreamingTool();
              }
            }
            if (parsed.status) {
              this.setConversationStatus(parsed.status);
              if (!isConversationRunningStatus(parsed.status)) {
                sawTerminalStatus = true;
                this.clearStreamingText();
                this.clearStreamingTool();
              }
            }
          }
          if (!controller.signal.aborted) {
            if (!sawTerminalStatus && isConversationRunningStatus(this.state.status)) {
              const reconId = streamConversationId != null ? streamConversationId : this.state.conversationId;
              if (reconId && this.streamReconnectCount < 3) {
                this.streamReconnectCount += 1;
                const delay = Math.pow(2, this.streamReconnectCount - 1) * 1e3;
                await new Promise((resolve) => setTimeout(resolve, delay));
                if (!controller.signal.aborted && isConversationRunningStatus(this.state.status)) {
                  try {
                    const scope = normalizeScope(this.client, this.scopeDefaults);
                    const scopedClient = applyPodScope(this.client, scope.podId);
                    const newStream = await scopedClient.conversations.resumeStream(reconId, {
                      pod_id: (_h = scope.podId) != null ? _h : void 0,
                      signal: controller.signal
                    });
                    await this.loadMessages({ conversationId: reconId, limit: 100 });
                    this.streamReconnectCount = 0;
                    return this.consume({
                      stream: newStream,
                      controller,
                      streamConversationId: reconId,
                      syncAfterStream
                    });
                  } catch {
                  }
                }
              }
              this.streamReconnectCount = 0;
              this.setConversationStatus("WAITING");
            }
            this.clearStreamingText();
            this.clearStreamingTool();
            const shouldSync = syncAfterStream != null ? syncAfterStream : this.options.syncOnTurnEnd;
            const syncConversationId = streamConversationId != null ? streamConversationId : this.state.conversationId;
            if (shouldSync && syncConversationId) {
              await this.refreshConversation(syncConversationId);
              await this.loadMessages({ conversationId: syncConversationId, limit: 100 });
            }
          }
        } catch (streamError) {
          if (!(streamError instanceof Error && streamError.name === "AbortError")) {
            const normalized = normalizeError(streamError, "Failed to stream conversation.");
            this.patch({ error: normalized });
            (_j = (_i = this.options).onError) == null ? void 0 : _j.call(_i, streamError);
          }
        } finally {
          if (this.abort === controller) {
            this.abort = null;
          }
          this.patch({ isStreaming: false });
        }
      });
      __publicField(this, "sendMessage", async (content, input = {}) => {
        var _a, _b, _c, _d;
        this.patch({ error: null });
        try {
          const resolvedConversation = await this.ensureConversation(input.conversationId);
          const resolvedConversationId = requireConversationId(resolvedConversation.id);
          this.cancel();
          const controller = new AbortController();
          this.abort = controller;
          const scope = normalizeScope(this.client, this.scopeDefaults);
          const scopedClient = applyPodScope(this.client, scope.podId);
          const stream = await scopedClient.conversations.sendMessageStream(
            resolvedConversationId,
            { content, metadata: (_a = input.metadata) != null ? _a : void 0 },
            { pod_id: (_b = scope.podId) != null ? _b : void 0, signal: controller.signal }
          );
          this.setConversationStatus("RUNNING");
          await this.consume({
            stream,
            controller,
            streamConversationId: resolvedConversationId,
            syncAfterStream: input.syncOnTurnEnd
          });
          return resolvedConversation;
        } catch (sendError) {
          const normalized = normalizeError(sendError, "Failed to send agent message.");
          this.patch({ error: normalized });
          (_d = (_c = this.options).onError) == null ? void 0 : _d.call(_c, sendError);
          throw normalized;
        }
      });
      __publicField(this, "resume", async (input) => {
        var _a, _b, _c, _d;
        this.patch({ error: null });
        try {
          const resumeInput = resolveResumeInput(input);
          const id = requireConversationId((_a = resumeInput.conversationId) != null ? _a : this.state.conversationId);
          if (resumeInput.onlyIfRunning && !isConversationRunningStatus(this.state.status)) {
            return;
          }
          this.cancel();
          const controller = new AbortController();
          this.abort = controller;
          const scope = normalizeScope(this.client, this.scopeDefaults);
          const scopedClient = applyPodScope(this.client, scope.podId);
          const stream = await scopedClient.conversations.resumeStream(id, {
            pod_id: (_b = scope.podId) != null ? _b : void 0,
            signal: controller.signal
          });
          this.setConversationStatus("RUNNING");
          await this.consume({
            stream,
            controller,
            streamConversationId: id,
            syncAfterStream: resumeInput.syncOnTurnEnd
          });
        } catch (resumeError) {
          const normalized = normalizeError(resumeError, "Failed to resume conversation.");
          this.patch({ error: normalized });
          (_d = (_c = this.options).onError) == null ? void 0 : _d.call(_c, resumeError);
          throw normalized;
        }
      });
      __publicField(this, "resumeIfRunning", async (explicitConversationId) => {
        const id = explicitConversationId != null ? explicitConversationId : this.state.conversationId;
        if (!id) return false;
        if (this.state.isStreaming) return false;
        const statusKey = normalizeConversationStatus(this.state.status);
        const resumeKey = `${id}:${statusKey != null ? statusKey : "UNKNOWN"}`;
        if (this.autoResumedKey === resumeKey) {
          return false;
        }
        const knownRunning = isConversationRunningStatus(this.state.status);
        if (!knownRunning) {
          const latestConversation = await this.refreshConversation(id);
          if (!latestConversation || !isConversationRunningStatus(latestConversation.status)) {
            return false;
          }
        }
        const previousResumeKey = this.autoResumedKey;
        this.autoResumedKey = resumeKey;
        try {
          await this.resume({ conversationId: id, onlyIfRunning: true });
          return true;
        } catch (error) {
          if (this.autoResumedKey === resumeKey) {
            this.autoResumedKey = previousResumeKey;
          }
          throw error;
        }
      });
      __publicField(this, "stop", async (explicitConversationId) => {
        var _a, _b, _c;
        this.patch({ error: null });
        try {
          const id = requireConversationId(explicitConversationId != null ? explicitConversationId : this.state.conversationId);
          const scope = normalizeScope(this.client, this.scopeDefaults);
          const scopedClient = applyPodScope(this.client, scope.podId);
          await scopedClient.conversations.stopRun(id, { pod_id: (_a = scope.podId) != null ? _a : void 0 });
          this.setConversationStatus("WAITING");
          this.clearStreamingText();
        } catch (stopError) {
          const normalized = normalizeError(stopError, "Failed to stop conversation.");
          this.patch({ error: normalized });
          (_c = (_b = this.options).onError) == null ? void 0 : _c.call(_b, stopError);
          throw normalized;
        }
      });
      var _a;
      this.options = options;
      this.state = {
        conversationId: (_a = options.initialConversationId) != null ? _a : null,
        conversation: null,
        status: void 0,
        messages: [],
        streamingText: "",
        streamingTool: null,
        isStreaming: false,
        error: null
      };
    }
    emit() {
      for (const listener of this.listeners) listener();
    }
    patch(partial) {
      this.state = { ...this.state, ...partial };
      this.emit();
    }
    get client() {
      return this.options.client;
    }
    get scopeDefaults() {
      var _a;
      return (_a = this.options.scope) != null ? _a : {};
    }
    setConversationStatus(nextStatus) {
      var _a, _b;
      const normalized = normalizeConversationStatus(nextStatus);
      this.patch({ status: normalized });
      if (normalized) {
        (_b = (_a = this.options).onStatus) == null ? void 0 : _b.call(_a, normalized);
      }
    }
    // -- streaming text buffering ----------------------------------------------
    appendStreamingToken(token) {
      if (!token) return;
      this.streamingBuffer += token;
      if (!this.pendingFlush) {
        this.pendingFlush = setTimeout(() => {
          this.pendingFlush = null;
          this.patch({ streamingText: this.streamingBuffer });
        }, 0);
      }
    }
    clearStreamingText() {
      if (this.pendingFlush) {
        clearTimeout(this.pendingFlush);
        this.pendingFlush = null;
      }
      this.streamingBuffer = "";
      this.patch({ streamingText: "" });
    }
    clearStreamingTool() {
      this.streamingToolToken = "";
      this.patch({ streamingTool: null });
    }
    async ensureConversation(overrideConversationId) {
      var _a;
      const existingId = overrideConversationId != null ? overrideConversationId : this.state.conversationId;
      if (existingId) {
        if (((_a = this.state.conversation) == null ? void 0 : _a.id) === existingId) {
          return this.state.conversation;
        }
        const existing = await this.refreshConversation(existingId);
        if (existing) return existing;
        throw new Error("Failed to resolve existing conversation.");
      }
      throw new Error("conversationId is required. Create a conversation before sending a message.");
    }
  };

  // src/core/agent/tool-names.ts
  var LEMMA_MCP_TOOL_PREFIXES = [
    "mcp.lemma_tools.",
    "mcp__lemma_tools__",
    "lemma_tools_",
    "lemma_tools.",
    "mcp.lemma-tools.",
    "mcp__lemma-tools__",
    "lemma-tools_",
    "lemma-tools."
  ];
  function normalizeAgentToolName(toolName) {
    let normalized = toolName.trim();
    const lower = normalized.toLowerCase();
    const providerPrefix = LEMMA_MCP_TOOL_PREFIXES.find((prefix) => lower.startsWith(prefix));
    if (providerPrefix) normalized = normalized.slice(providerPrefix.length);
    if (normalized.toLowerCase().startsWith("lemma_")) {
      normalized = normalized.slice("lemma_".length);
    }
    return normalized;
  }

  // src/core/agent/output.ts
  function isRecord4(value) {
    return !!value && typeof value === "object" && !Array.isArray(value);
  }
  function createdAtMs(message) {
    const raw = message.createdAt instanceof Date ? message.createdAt.toISOString() : message.created_at;
    if (!raw) return null;
    const timestamp = new Date(raw).getTime();
    return Number.isFinite(timestamp) ? timestamp : null;
  }
  function latestFirst(messages) {
    const withTimestamps = messages.every((message) => createdAtMs(message) !== null);
    if (!withTimestamps) return [...messages].reverse();
    return [...messages].sort((a, b) => {
      var _a, _b;
      const aTime = (_a = createdAtMs(a)) != null ? _a : 0;
      const bTime = (_b = createdAtMs(b)) != null ? _b : 0;
      return bTime - aTime;
    });
  }
  function extractJsonObject(text) {
    const trimmed = text.trim();
    if (!trimmed) return null;
    const fenced = trimmed.match(/```(?:json)?\s*([\s\S]*?)```/i);
    const objectStart = trimmed.indexOf("{");
    const objectEnd = trimmed.lastIndexOf("}");
    const candidates = [
      fenced == null ? void 0 : fenced[1],
      trimmed,
      objectStart >= 0 && objectEnd > objectStart ? trimmed.slice(objectStart, objectEnd + 1) : void 0
    ].filter((candidate) => !!candidate && candidate.trim().length > 0);
    for (const candidate of candidates) {
      try {
        const parsed = JSON.parse(candidate.trim());
        if (isRecord4(parsed)) return parsed;
      } catch {
      }
    }
    return null;
  }
  function messageTextCandidates(message, options = {}) {
    const candidates = [];
    if (typeof message.text === "string" && message.text.trim()) {
      const kind = typeof message.kind === "string" ? message.kind : "";
      const shouldUseText = kind !== "TOOL_CALL" && kind !== "tool_call" && kind !== "TOOL_RETURN" && kind !== "tool_return" && (kind !== "THINKING" && kind !== "thinking" || options.includeReasoning === true);
      if (shouldUseText) {
        candidates.push(message.text.trim());
      }
    }
    if (typeof message.content === "string" && message.content.trim()) {
      candidates.push(message.content.trim());
    }
    const textParts = (message.parts || []).filter((part) => (part.type === "text" || options.includeReasoning === true && part.type === "reasoning") && typeof part.text === "string").map((part) => {
      var _a;
      return (_a = part.text) == null ? void 0 : _a.trim();
    }).filter((text) => !!text);
    candidates.push(...textParts);
    return candidates.filter((candidate) => candidate.trim().length > 0);
  }
  function isFinalResultToolName(toolName) {
    const normalized = normalizeAgentToolName(toolName).toLowerCase().replace(/[.\-:]/g, "_");
    return normalized === "final_result" || normalized === "final_answer";
  }
  function unwrapFinalResultPayload(value) {
    if (!isRecord4(value)) return value;
    const directKeys = [
      "structured_output",
      "structuredOutput",
      "output_data",
      "outputData",
      "final_result",
      "finalResult",
      "final_answer",
      "finalAnswer",
      "answer",
      "result",
      "output",
      "data",
      "value"
    ];
    for (const key of directKeys) {
      if (typeof value[key] !== "undefined") {
        return unwrapFinalResultPayload(value[key]);
      }
    }
    const operationalKeys = /* @__PURE__ */ new Set([
      "success",
      "message",
      "error",
      "status",
      "final_answer_status",
      "finalAnswerStatus",
      "final_answer_tool_name",
      "finalAnswerToolName",
      "tool_name",
      "toolName",
      "tool_call_id",
      "toolCallId",
      "is_final_answer",
      "isFinalAnswer"
    ]);
    const meaningfulEntries = Object.entries(value).filter(([key]) => !operationalKeys.has(key));
    if (meaningfulEntries.length > 0) {
      return Object.fromEntries(meaningfulEntries);
    }
    if (typeof value.message === "string") {
      const parsed = extractJsonObject(value.message);
      return parsed != null ? parsed : value.message;
    }
    return value;
  }
  function getMessageMetadata(message) {
    if (isRecord4(message.metadata)) return message.metadata;
    if (isRecord4(message.message_metadata)) return message.message_metadata;
    return null;
  }
  function toFinalOutputRecord(value) {
    const unwrapped = unwrapFinalResultPayload(value);
    if (isRecord4(unwrapped)) return unwrapped;
    if (typeof unwrapped === "string") {
      const parsed = extractJsonObject(unwrapped);
      return parsed != null ? parsed : { result: unwrapped };
    }
    if (Array.isArray(unwrapped)) return { result: unwrapped };
    if (typeof unwrapped === "number" || typeof unwrapped === "boolean") return { result: unwrapped };
    return null;
  }
  function finalOutputFromMetadata(messages) {
    var _a;
    for (const message of latestFirst(messages)) {
      const metadata = getMessageMetadata(message);
      if ((metadata == null ? void 0 : metadata.is_final_answer) !== true && (metadata == null ? void 0 : metadata.isFinalAnswer) !== true) continue;
      const structured = toFinalOutputRecord((_a = metadata.structured_output) != null ? _a : metadata.structuredOutput);
      if (structured) return structured;
      const content = typeof message.text === "string" ? message.text : typeof message.content === "string" ? message.content : "";
      const parsed = extractJsonObject(content);
      if (parsed) return parsed;
    }
    return null;
  }
  function finalOutputFromMarkedText(messages) {
    var _a, _b;
    for (const message of latestFirst(messages)) {
      const metadata = getMessageMetadata(message) || {};
      const toolName = String((_b = (_a = message.tool_name) != null ? _a : metadata.tool_name) != null ? _b : "");
      const isMarkedFinal = metadata.is_final_answer === true || metadata.isFinalAnswer === true || isFinalResultToolName(toolName);
      if (!isMarkedFinal) continue;
      for (const text of messageTextCandidates(message)) {
        const parsed = extractJsonObject(text);
        if (parsed) return parsed;
      }
    }
    return null;
  }
  function finalOutputFromToolInvocations(messages) {
    var _a, _b;
    const toolInvocations = messages.flatMap((message) => [
      ...(message.parts || []).filter((part) => part.type === "tool").map((part) => part.toolInvocation).filter(Boolean),
      ...message.toolInvocations || []
    ]);
    for (const invocation of [...toolInvocations].reverse()) {
      const toolName = String((_b = (_a = invocation.toolName) != null ? _a : invocation.tool_name) != null ? _b : "");
      if (!isFinalResultToolName(toolName)) continue;
      const candidates = [invocation.result, invocation.args].map(toFinalOutputRecord).filter((candidate) => candidate !== null);
      if (candidates.length > 0) return candidates[0];
    }
    return null;
  }
  function finalOutputFromRawToolMessages(messages) {
    var _a, _b;
    for (const message of latestFirst(messages)) {
      const metadata = getMessageMetadata(message) || {};
      const toolName = String((_b = (_a = message.tool_name) != null ? _a : metadata.tool_name) != null ? _b : "");
      if (!isFinalResultToolName(toolName)) continue;
      const candidates = [
        message.tool_result,
        message.tool_args,
        metadata.structured_output,
        metadata.structuredOutput
      ].map(toFinalOutputRecord);
      const output = candidates.find((candidate) => candidate !== null);
      if (output) return output;
    }
    return null;
  }
  function finalOutputFromJsonText(messages) {
    for (const message of latestFirst(messages)) {
      const role = String(message.role || "").toLowerCase();
      if (role === "user") continue;
      for (const text of messageTextCandidates(message, { includeReasoning: true })) {
        const parsed = extractJsonObject(text);
        if (parsed) return parsed;
      }
    }
    return null;
  }
  function extractAgentFinalOutput(messages, options = {}) {
    var _a, _b, _c, _d;
    return (_d = (_c = (_b = (_a = finalOutputFromMetadata(messages)) != null ? _a : finalOutputFromToolInvocations(messages)) != null ? _b : finalOutputFromRawToolMessages(messages)) != null ? _c : finalOutputFromMarkedText(messages)) != null ? _d : options.parseTextFallback ? finalOutputFromJsonText(messages) : null;
  }

  // src/core/agent/task.ts
  function agentActivityLabel(state) {
    var _a;
    const running = state.isStreaming || isConversationRunningStatus(state.status);
    if (!running) return "";
    if ((_a = state.streamingTool) == null ? void 0 : _a.toolName) return `Using ${state.streamingTool.toolName}`;
    return "Working\u2026";
  }
  function selectAgentTask(state, options = {}) {
    const outputs = selectAgentOutputs(state);
    const running = state.isStreaming || isConversationRunningStatus(state.status);
    let status;
    if (state.error) status = "error";
    else if (running) status = "running";
    else if (outputs.finalOutput) status = "done";
    else status = "idle";
    return {
      status,
      isRunning: running,
      isDone: status === "done",
      activity: agentActivityLabel(state),
      streamingText: state.streamingText,
      outputText: outputs.finalOutputText,
      output: options.parseOutput === false || running ? null : extractAgentFinalOutput(state.messages, {
        parseTextFallback: true
      }),
      finalMessage: outputs.finalOutput,
      error: state.error
    };
  }

  // src/ui/client.ts
  function clientConstructor() {
    const globalLemma = globalThis.LemmaClient;
    const ctor = globalLemma == null ? void 0 : globalLemma.LemmaClient;
    if (!ctor) {
      throw new Error(
        "window.LemmaClient is unavailable \u2014 load /public/sdk/lemma-client.js before lemma-ui.js."
      );
    }
    return ctor;
  }
  function resolveUiClient(podId) {
    const Ctor = clientConstructor();
    return podId ? new Ctor({ podId }) : new Ctor();
  }

  // src/ui/shared.ts
  var HTMLElementBase = typeof HTMLElement !== "undefined" ? HTMLElement : class {
  };
  function escapeHtml(value) {
    return value.replace(
      /[&<>"']/g,
      (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]
    );
  }
  var BASE_STYLES = `
:host {
  --lemma-font: ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --lemma-fg: #18181b;
  --lemma-muted: #71717a;
  --lemma-bg: #ffffff;
  --lemma-surface: #f4f4f5;
  --lemma-border: #e4e4e7;
  --lemma-accent: #4f46e5;
  --lemma-accent-fg: #ffffff;
  --lemma-radius: 12px;
  --lemma-gap: 10px;
  display: block;
  font-family: var(--lemma-font);
  color: var(--lemma-fg);
  font-size: 14px;
  line-height: 1.5;
}
* { box-sizing: border-box; }
button {
  font: inherit;
  cursor: pointer;
  border: 0;
  border-radius: calc(var(--lemma-radius) - 6px);
  padding: 8px 14px;
  background: var(--lemma-accent);
  color: var(--lemma-accent-fg);
  transition: opacity 120ms ease;
}
button:disabled { opacity: 0.5; cursor: default; }
.activity { color: var(--lemma-muted); }
`;

  // src/ui/lemma-agent-task.ts
  var STYLE = `
${BASE_STYLES}
.wrap {
  display: flex;
  flex-direction: column;
  gap: var(--lemma-gap);
  padding: 16px;
  background: var(--lemma-bg);
  border: 1px solid var(--lemma-border);
  border-radius: var(--lemma-radius);
}
.stream { white-space: pre-wrap; color: var(--lemma-muted); }
.output {
  margin: 0;
  padding: 12px;
  background: var(--lemma-surface);
  border-radius: calc(var(--lemma-radius) - 4px);
  white-space: pre-wrap;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
}
.output.text { font-family: var(--lemma-font); font-size: 14px; }
.error { color: #b91c1c; }
.hint { color: var(--lemma-muted); }
.row { display: flex; justify-content: flex-end; }
`;
  var LemmaAgentTaskElement = class extends HTMLElementBase {
    constructor() {
      super();
      __publicField(this, "controller", null);
      __publicField(this, "unsubscribe", null);
      __publicField(this, "dispatchedFor", "");
      __publicField(this, "view");
      this.view = this.attachShadow({ mode: "open" });
    }
    static get observedAttributes() {
      return ["agent", "pod", "input", "auto-run", "parse-output"];
    }
    connectedCallback() {
      this.render();
      if (this.hasAttribute("auto-run")) void this.run();
    }
    disconnectedCallback() {
      this.teardown();
    }
    attributeChangedCallback(name) {
      if (name === "agent" || name === "pod") {
        this.teardown();
      }
      if (this.isConnected) this.render();
    }
    /** Start a fresh run. Without an argument it uses the `input` attribute. */
    async run(input) {
      var _a;
      const controller = this.ensureController();
      this.dispatchedFor = "";
      await controller.createConversation({ setActive: true });
      await controller.sendMessage((_a = input != null ? input : this.getAttribute("input")) != null ? _a : "");
    }
    ensureController() {
      if (!this.controller) {
        this.controller = new AgentController({
          client: resolveUiClient(this.getAttribute("pod")),
          scope: { agentName: this.getAttribute("agent") }
        });
        this.unsubscribe = this.controller.subscribe(() => this.render());
      }
      return this.controller;
    }
    teardown() {
      var _a, _b;
      (_a = this.unsubscribe) == null ? void 0 : _a.call(this);
      this.unsubscribe = null;
      (_b = this.controller) == null ? void 0 : _b.destroy();
      this.controller = null;
    }
    render() {
      var _a, _b, _c, _d, _e, _f;
      const parseOutput = this.getAttribute("parse-output") !== "false";
      const view = this.controller ? selectAgentTask(this.controller.getState(), { parseOutput }) : null;
      const status = (_a = view == null ? void 0 : view.status) != null ? _a : "idle";
      let body;
      if (status === "running") {
        const stream = (view == null ? void 0 : view.streamingText) ? `<div part="stream" class="stream">${escapeHtml(view.streamingText)}</div>` : "";
        body = `<div part="activity" class="activity">${escapeHtml((view == null ? void 0 : view.activity) || "Working\u2026")}</div>${stream}`;
      } else if (status === "done") {
        body = (view == null ? void 0 : view.output) != null ? `<pre part="output" class="output">${escapeHtml(JSON.stringify(view.output, null, 2))}</pre>` : `<div part="output" class="output text">${escapeHtml((view == null ? void 0 : view.outputText) || "")}</div>`;
      } else if (status === "error") {
        body = `<div part="error" class="error">${escapeHtml(((_b = view == null ? void 0 : view.error) == null ? void 0 : _b.message) || "Something went wrong.")}</div>`;
      } else {
        body = `<div part="hint" class="hint">Ready to run.</div>`;
      }
      const showRun = !this.hasAttribute("auto-run") && status !== "running";
      const runRow = showRun ? `<div class="row"><button part="run-button" data-run type="button">${status === "idle" ? "Run" : "Run again"}</button></div>` : "";
      this.view.innerHTML = `<style>${STYLE}</style><div part="root" class="wrap">${body}${runRow}</div>`;
      (_c = this.view.querySelector("[data-run]")) == null ? void 0 : _c.addEventListener("click", () => void this.run());
      if (status === "done" && this.controller) {
        const key = (_d = this.controller.getState().conversationId) != null ? _d : "";
        if (key && this.dispatchedFor !== key) {
          this.dispatchedFor = key;
          this.dispatchEvent(
            new CustomEvent("lemma-output", {
              detail: { output: (_e = view == null ? void 0 : view.output) != null ? _e : null, text: (_f = view == null ? void 0 : view.outputText) != null ? _f : "" },
              bubbles: true
            })
          );
        }
      }
    }
  };

  // src/ui/lemma-agent-thread.ts
  var STYLE2 = `
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
  var LemmaAgentThreadElement = class extends HTMLElementBase {
    constructor() {
      super();
      __publicField(this, "controller", null);
      __publicField(this, "unsubscribe", null);
      __publicField(this, "draft", "");
      __publicField(this, "view");
      this.view = this.attachShadow({ mode: "open" });
    }
    static get observedAttributes() {
      return ["agent", "pod", "conversation-id"];
    }
    connectedCallback() {
      const conversationId = this.getAttribute("conversation-id");
      if (conversationId) {
        const controller = this.ensureController();
        controller.setConversationId(conversationId);
        void controller.refreshConversation(conversationId);
        void controller.loadMessages({ conversationId, limit: 100 });
      }
      this.render();
    }
    disconnectedCallback() {
      this.teardown();
    }
    attributeChangedCallback(name) {
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
    async send(text) {
      const content = text.trim();
      if (!content) return;
      const controller = this.ensureController();
      if (!controller.getState().conversationId) {
        await controller.createConversation({ setActive: true });
      }
      await controller.sendMessage(content);
    }
    ensureController() {
      if (!this.controller) {
        this.controller = new AgentController({
          client: resolveUiClient(this.getAttribute("pod")),
          scope: { agentName: this.getAttribute("agent") }
        });
        this.unsubscribe = this.controller.subscribe(() => this.render());
      }
      return this.controller;
    }
    teardown() {
      var _a, _b;
      (_a = this.unsubscribe) == null ? void 0 : _a.call(this);
      this.unsubscribe = null;
      (_b = this.controller) == null ? void 0 : _b.destroy();
      this.controller = null;
    }
    renderMessages() {
      var _a, _b;
      const state = (_a = this.controller) == null ? void 0 : _a.getState();
      const rows = ((_b = state == null ? void 0 : state.messages) != null ? _b : []).map((message) => {
        const role = String(message.role || "").toLowerCase();
        const kind = String(message.kind || "");
        if (kind === "TOOL_CALL" || kind === "tool_call" || kind === "TOOL_RETURN" || kind === "tool_return") {
          return `<div part="event" class="event">\u2699 ${escapeHtml(String(message.tool_name || "tool"))}</div>`;
        }
        if (kind === "THINKING" || kind === "thinking") return "";
        const text = conversationMessageText(message);
        if (!text) return "";
        const cls = role === "user" ? "user" : "assistant";
        return `<div part="message" class="msg ${cls}" data-role="${cls}">${escapeHtml(text)}</div>`;
      }).join("");
      if (state == null ? void 0 : state.isStreaming) {
        const streaming = state.streamingText ? `<div part="message" class="msg assistant" data-role="assistant">${escapeHtml(state.streamingText)}</div>` : `<div part="activity" class="activity">Working\u2026</div>`;
        return rows + streaming;
      }
      return rows;
    }
    render() {
      var _a;
      const value = escapeHtml(this.draft);
      this.view.innerHTML = `<style>${STYLE2}</style><div part="root" class="wrap"><div part="messages" class="messages">${this.renderMessages()}</div><form part="composer" class="composer" data-send><input part="input" data-input type="text" placeholder="Message\u2026" value="${value}" /><button part="send" type="submit">Send</button></form></div>`;
      const input = this.view.querySelector("[data-input]");
      input == null ? void 0 : input.addEventListener("input", () => {
        this.draft = input.value;
      });
      (_a = this.view.querySelector("[data-send]")) == null ? void 0 : _a.addEventListener("submit", (event) => {
        event.preventDefault();
        const text = this.draft;
        this.draft = "";
        void this.send(text);
      });
      const messages = this.view.querySelector(".messages");
      if (messages) messages.scrollTop = messages.scrollHeight;
    }
  };

  // src/ui/index.ts
  var REGISTRY = [
    ["lemma-agent-task", LemmaAgentTaskElement],
    ["lemma-agent-thread", LemmaAgentThreadElement]
  ];
  function defineLemmaElements(registry) {
    const target = registry != null ? registry : typeof customElements !== "undefined" ? customElements : void 0;
    if (!target) return;
    for (const [name, ctor] of REGISTRY) {
      if (!target.get(name)) target.define(name, ctor);
    }
  }

  // src/browser-ui.ts
  defineLemmaElements();
  return __toCommonJS(browser_ui_exports);
})();
