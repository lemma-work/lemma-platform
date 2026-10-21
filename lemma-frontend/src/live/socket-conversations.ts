"use client";

import type { LemmaClient } from "lemma-sdk";
import { socketRunStream, SocketUnavailable } from "./conversation-stream";
import { liveAvailable, podLive } from "./pod-live";

type Conversations = LemmaClient["conversations"];

/** The client the conversation pane hands the SDK's session, with a run's
 *  stream read off the pod's live socket. Everything else is the client as
 *  it was; when the socket cannot carry a run -- not configured, refused, too
 *  slow to answer -- the run streams over SSE exactly as before. */
export function withSocketConversations(client: LemmaClient, podId: string): LemmaClient {
    const conversations = client.conversations;
    const deps = () => ({
        live: podLive(podId),
        status: async (conversationId: string) => (await conversations.get(conversationId, { pod_id: podId })).status,
        append: (conversationId: string, payload: unknown) =>
            conversations.appendMessage(conversationId, payload as Parameters<Conversations["appendMessage"]>[1], { pod_id: podId }),
    });
    const resumeStream: Conversations["resumeStream"] = async (conversationId, options = {}) => {
        if (liveAvailable() && (!options.pod_id || options.pod_id === podId)) {
            try {
                return await socketRunStream(deps(), { conversationId, agentRunId: options.agent_run_id ?? null, signal: options.signal });
            } catch (error) {
                if (!(error instanceof SocketUnavailable)) throw error;
            }
        }
        return conversations.resumeStream(conversationId, options);
    };
    const sendMessageStream: Conversations["sendMessageStream"] = async (conversationId, payload, options = {}) => {
        if (liveAvailable() && (!options.pod_id || options.pod_id === podId)) {
            try {
                return await socketRunStream(deps(), { conversationId, send: payload, signal: options.signal });
            } catch (error) {
                if (!(error instanceof SocketUnavailable)) throw error;
            }
        }
        return conversations.sendMessageStream(conversationId, payload, options);
    };
    const wrapped = new Proxy(conversations, {
        get(target, name, receiver) {
            if (name === "resumeStream") return resumeStream;
            if (name === "sendMessageStream") return sendMessageStream;
            const value = Reflect.get(target, name, receiver);
            return typeof value === "function" ? value.bind(target) : value;
        },
    });
    return new Proxy(client, {
        get(target, name) {
            if (name === "conversations") return wrapped;
            const value = Reflect.get(target, name, target);
            return typeof value === "function" ? value.bind(target) : value;
        },
    });
}
