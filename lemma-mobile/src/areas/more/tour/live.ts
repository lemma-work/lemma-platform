import type { LiveContext } from "../../context";
import type { TourSource } from "./source";

/** The account the tour is for, as `users.current` says it. */
export function tourLive(context: LiveContext): TourSource {
    return {
        async account() {
            const user = (await context.client().users.current()) as { id?: string | null; created_at?: string | null } | null;
            return { id: user?.id ?? null, createdAt: user?.created_at ?? null };
        },
    };
}
