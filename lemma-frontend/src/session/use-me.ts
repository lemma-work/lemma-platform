"use client";

import { source } from "@/data";
import { useSession } from "./session";

/** The signed-in person's user id — what a run or a schedule names as its
 *  owner. The sample has no session, so it answers as its one sample user. */
export function useMe(): string | null {
    const { user } = useSession();
    return user?.id ?? (source.label === "sample" ? "sample-user" : null);
}
