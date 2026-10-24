import type { LiveContext } from "../../context";
import { withSocketConversations } from "../../../live/socket-conversations";
import { configuredFrom, voiceProviderOf } from "./model";
import { defaultAgentOf, profileFrom } from "./profile";
import type { CallSource } from "./source";
import { ConversationRouter } from "@web/call/conversation-router";
import { podBrief } from "@web/call/pod-brief";

/** A call, against the server. Two reads go to the web server rather than
 *  the API, and neither carries a session: whether it can carry a call
 *  (`/api/call/config`), and which voice model it runs (`site-config.js`).
 *  Everything that needs one is made here, with the phone's own; nothing of
 *  it reaches the page that holds the microphone. */
export function callLive(context: LiveContext): CallSource {
    const web = context.server.web;
    return {
        async readiness() {
            /* A failed probe is a no, as on the web; one that never reached
               the server is a failure, said as one by the screen. */
            const [configured, provider] = await Promise.all([
                fetch(`${web}/api/call/config`, { cache: "no-store" })
                    .then((response) => (response.ok ? response.json().then(configuredFrom, () => false) : false)),
                fetch(`${web}/site-config.js`, { cache: "no-store" })
                    .then((response) => (response.ok ? response.text() : ""))
                    .then(voiceProviderOf, () => voiceProviderOf("")),
            ]);
            return { configured, provider };
        },

        /* The web's `getProfile` reads, all at once: a Lemma that cannot be
           read is a call without the background, never no call. */
        async brief(podId, name) {
            const client = context.client(podId);
            const quietly = <T,>(read: Promise<T>) => read.catch(() => null);
            const [pod, agents, schedules, apps, tables, functions, workflows] = await Promise.all([
                quietly(client.pods.get(podId)),
                quietly(client.agents.list({ limit: 100 })),
                quietly(client.schedules.list({ limit: 12 })),
                quietly(client.apps.list({ limit: 12 })),
                quietly(client.tables.list({ limit: 50 })),
                quietly(client.functions.list({ limit: 50 })),
                quietly(client.workflows.list({ limit: 50 })),
            ]);
            const responder = defaultAgentOf(agents);
            const defaultAgent = responder ? await quietly(client.agents.get(responder)) : null;
            const profile = profileFrom({ podId, pod, agents, defaultAgent, schedules, apps, tables, functions, workflows });
            return podBrief(profile, name);
        },

        session(podId, changed) {
            return new ConversationRouter(withSocketConversations(context.client(podId), podId), podId, changed);
        },

        engine: null,
    };
}
