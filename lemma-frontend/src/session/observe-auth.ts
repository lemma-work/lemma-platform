import type { AuthManager, AuthState } from "lemma-sdk";
import type { Person } from "./who";

type SessionSource = Pick<AuthManager, "getState" | "subscribe" | "checkAuth" | "isTokenMode">;

/** The cross-origin cookie probe belongs only to initial session discovery.
 * Later unauthorised events must not revive a session that has ended. */
export function observeAuth(
    auth: SessionSource,
    probe: () => Promise<Person | null>,
    publish: (state: AuthState) => void,
): () => void {
    let live = true;
    let revision = 0;
    let probed = false;
    let authenticated = auth.getState().status === "authenticated";
    const receive = (next: AuthState) => {
        const current = ++revision;
        if (next.status === "authenticated") authenticated = true;
        if (next.status !== "unauthenticated" || auth.isTokenMode || probed || authenticated) {
            publish(next);
            return;
        }
        probed = true;
        publish({ status: "loading", user: null });
        void probe().catch(() => null).then(user => {
            if (!live || current !== revision) return;
            authenticated = Boolean(user);
            publish(user ? { status: "authenticated", user } : next);
        });
    };
    const unsubscribe = auth.subscribe(receive);
    receive(auth.getState());
    if (auth.getState().status === "loading") void auth.checkAuth().catch(() => undefined);
    return () => { live = false; revision++; unsubscribe(); };
}
