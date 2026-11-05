import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { CONVERSATION_ASKS, conversationAsksQuery } from "./waiting-on-you";

/** The conversations paused on you, across these pods.
 *
 *  The inbox beside the bell, the rail and Home each used to build this
 *  query themselves. They agreed on the key and drifted on the rest, and
 *  none of them refreshed until somebody navigated. One hook, so all three
 *  poll the same way and still share one request. */
export function useConversationAsks(pods: readonly Pod[], { enabled = true }: { enabled?: boolean } = {}) {
    return useQuery(conversationAsksQuery(pods, source.label === "sample", enabled));
}

/** Re-read the asks when the open conversation starts or stops waiting.
 *
 *  `waitingOn` is the id of the card the conversation on screen is blocked
 *  on, or null. A new id is a new ask the lists have not heard of; a null
 *  after an id is one answered — by this person here or by anyone anywhere,
 *  which the explicit answer path cannot see. Either way the rail is wrong
 *  until the next poll, and this is the one place that knows sooner.
 *
 *  Not on the first render: a conversation opened from a cached transcript
 *  already waiting is not news. One whose transcript arrives after it opens
 *  does read once, which is a single request and may well be news. */
export function useAsksFollowWaiting(waitingOn: string | null) {
    const cache = useQueryClient();
    const seen = useRef(waitingOn);
    useEffect(() => {
        if (seen.current === waitingOn) return;
        seen.current = waitingOn;
        void cache.invalidateQueries({ queryKey: [CONVERSATION_ASKS] });
    }, [waitingOn, cache]);
}
