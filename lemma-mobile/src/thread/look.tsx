import { createContext, useContext, useMemo, type ReactNode } from "react";
import { identityOf, type Identity } from "../ui/identity";
import { useTheme } from "../ui/theme";
import { color } from "../ui/tokens";
import type { StepTime } from "./steps";

/** What a chat with a Lemma carries of it all the way down: its colour
 *  (`ui/identity.ts`), for buttons, links and the send button, and when each
 *  of the run's steps happened, for the steps list. Status keeps violet and
 *  amber; this is never a status. */

interface Look extends Identity {
    timeOf: (callId: string | undefined) => StepTime | undefined;
}

const LookContext = createContext<Look | null>(null);

export function ChatLook({ podId, timeOf, children }: { podId: string; timeOf: Look["timeOf"]; children: ReactNode }) {
    const { scheme } = useTheme();
    const look = useMemo(() => ({ ...identityOf(podId, scheme), timeOf }), [podId, scheme, timeOf]);
    return <LookContext.Provider value={look}>{children}</LookContext.Provider>;
}

/** The chat's look, or the app's own ink outside a chat. */
export function useLook(): Look {
    return useContext(LookContext) ?? { ink: color.ink, onInk: color.onInk, wash: color.paper, soft: color.wash, timeOf: () => undefined };
}
