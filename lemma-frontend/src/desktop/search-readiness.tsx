"use client";

import { useQuery } from "@tanstack/react-query";
import { apiUrl, hasApiUrl } from "@/session/client";
import { SettingRow } from "./this-mac-settings";
import { embeddingsStatus, searchModelRow } from "./search-model";

/** One Overview row: whether this Mac's search model is ready.
 *
 *  Polled briskly while it is downloading or retrying, so the row turns to
 *  Ready on its own, and slowly once it is settled. Hidden when the backend
 *  cannot be asked or does not embed locally — a row that guesses is worse
 *  than none. */
export function SearchReadinessRow() {
    const capability = useQuery({
        queryKey: ["this-mac-search-model"],
        enabled: hasApiUrl(),
        queryFn: async () => {
            const response = await fetch(apiUrl().replace(/\/$/, "") + "/health/capabilities", { cache: "no-store" });
            if (!response.ok) return null;
            return embeddingsStatus(await response.json());
        },
        refetchInterval: (query) => (query.state.data === "ready" || query.state.data == null ? 60_000 : 5_000),
        retry: 0,
    });
    const row = searchModelRow(capability.data);
    if (!row) return null;
    return (
        <SettingRow name="Search model" consequence={row.consequence}>
            <span className={"thismac-search-model thismac-search-model--" + row.state} role="status">{row.value}</span>
        </SettingRow>
    );
}
