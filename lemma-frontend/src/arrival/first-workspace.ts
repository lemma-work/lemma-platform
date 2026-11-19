import type { FirstWorkspaceWanted } from "@/data/types";

/** What a way in asks the server's first-workspace rule for. One place, so
 *  every door that makes somebody's first organization asks the same thing.
 *
 *  On a local install nobody else can reach the server yet, so the
 *  organization is a workspace of one whatever the address says: opening it
 *  to a whole email domain there would offer it to colleagues who cannot get
 *  in. `own` is "start your own organization instead" of their company's. */
export function firstWorkspaceWanted({ own = false, local }: { own?: boolean; local: boolean }): FirstWorkspaceWanted {
    return {
        ...(own ? { ownOrganization: true } : {}),
        ...(local ? { team: false } : {}),
    };
}
