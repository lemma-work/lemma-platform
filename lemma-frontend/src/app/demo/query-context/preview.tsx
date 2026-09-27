"use client";

import { useEffect, useState } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { LemmaClient } from "lemma-sdk";
import { useTableGet } from "lemma-sdk/react";

const tableKey = ["table", "query-preview", "get", "tasks"];
const client = new LemmaClient({ apiUrl: "https://api.example.invalid", podId: "query-preview" });

function TableProbe() {
    const cache = useQueryClient();
    const [ready, setReady] = useState(false);
    useEffect(() => setReady(true), []);
    // Disabled requests keep this check independent of authentication and API availability.
    const table = useTableGet(client, undefined, "tasks", { enabled: false });
    const shared = table.data === cache.getQueryData(tableKey);
    return <main>
        <h1>SDK query context</h1>
        <p role="status">{shared && table.isSuccess ? "Shared query cache connected" : "Query caches do not match"}</p>
        <button disabled={!ready} onClick={() => cache.setQueryData(tableKey, { name: "Updated through the frontend" })}>Update shared cache</button>
        <pre>{JSON.stringify(table.data)}</pre>
    </main>;
}

export function QueryContextPreview() {
    const [cache] = useState(() => {
        const result = new QueryClient();
        result.setQueryData(tableKey, { name: "Tasks" });
        return result;
    });
    return <QueryClientProvider client={cache}><TableProbe /></QueryClientProvider>;
}
