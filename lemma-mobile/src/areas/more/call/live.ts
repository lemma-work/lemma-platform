import { callSocketUrl, openCallSocket } from "@web/call/call-socket";
import { historyBrief } from "@web/call/history-brief";
import { podBrief } from "@web/call/pod-brief";
import { buildTurns, type RawMessage } from "@web/thread/turns";
import { freshAccess } from "../../../session/auth";
import type { LiveContext } from "../../context";
import { configuredFrom } from "./model";
import { defaultAgentOf, profileFrom } from "./profile";
import type { CallSource } from "./source";

/** React Native's WebSocket takes headers as a third argument, which the
 *  DOM's type does not know of. */
const NativeSocket = WebSocket as unknown as new (url: string, protocols: null, options: { headers: Record<string, string> } | null) => WebSocket;

/** A call, against the server, with the phone's own session.
 *
 *  The socket carries the token in an `Authorization` header as well as the
 *  address, as the pod's live socket does: Android's native networking
 *  shares the WebView's cookie jar, and a stale cookie there must not win.
 *  Nothing of the session reaches the page that holds the microphone. */
export function callLive(context: LiveContext): CallSource {
    return {
        sound: true,

        async callable(podId) {
            try {
                return configuredFrom(await context.client(podId).calls.config());
            } catch {
                return false;
            }
        },

        async open(podId, name) {
            const opened = await context.client(podId).calls.open({ display_name: name, title: `Call with ${name}` });
            return opened.call_id;
        },

        /* The web's `getProfile` reads, all at once, and the chat the call
           grew out of: either one unread is a call with less background,
           never no call. */
        async brief(podId, name, conversationId) {
            const client = context.client(podId);
            const quietly = <T,>(read: Promise<T>) => read.catch(() => null);
            const [pod, agents, schedules, apps, tables, functions, workflows, page] = await Promise.all([
                quietly(client.pods.get(podId)),
                quietly(client.agents.list({ limit: 100 })),
                quietly(client.schedules.list({ limit: 12 })),
                quietly(client.apps.list({ limit: 12 })),
                quietly(client.tables.list({ limit: 50 })),
                quietly(client.functions.list({ limit: 50 })),
                quietly(client.workflows.list({ limit: 50 })),
                conversationId ? quietly(client.conversations.messages.list(conversationId, { pod_id: podId, limit: 80 })) : Promise.resolve(null),
            ]);
            const responder = defaultAgentOf(agents);
            const defaultAgent = responder ? await quietly(client.agents.get(responder)) : null;
            const profile = profileFrom({ podId, pod, agents, defaultAgent, schedules, apps, tables, functions, workflows });
            const history = page ? historyBrief(buildTurns(page.items as unknown as RawMessage[]), name) : "";
            return [podBrief(profile, name), history].filter(Boolean).join("\n\n");
        },

        async connect(podId, callId, start, handlers) {
            const token = await freshAccess();
            const url = callSocketUrl(context.server.api, podId, callId, token);
            return openCallSocket(url, start, handlers, (address) => new NativeSocket(address, null, token ? { headers: { Authorization: `Bearer ${token}` } } : null));
        },
    };
}
