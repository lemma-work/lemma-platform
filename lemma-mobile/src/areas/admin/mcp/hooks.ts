import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { adminKey, useAdminSource } from "../hooks";
import type { AboutLine } from "../types";
import { mcpLine } from "./model";

/** This part's reads and writes, through react-query. */
export function useMcpSource() {
    return useAdminSource().mcp;
}

function useKey(podId: string, ...rest: readonly unknown[]) {
    return adminKey(useCacheKey(), "mcp", podId, ...rest);
}

/** The Lemma's MCP address: undefined while asked, null where this server
 *  does not offer MCP. A deployment setting, so five minutes is fresh. */
export function useMcpEndpoint(podId: string) {
    const source = useMcpSource();
    const key = useKey(podId, "endpoint");
    return useQuery({ queryKey: key, queryFn: () => source.endpoint(podId), staleTime: 5 * 60_000, retry: false });
}

/** The apps connected to it, read once there is an address to connect to. */
export function useGrants(podId: string, offered: boolean) {
    const source = useMcpSource();
    const key = useKey(podId, "grants");
    return useQuery({ queryKey: key, queryFn: () => source.grants(podId), enabled: offered, staleTime: 5 * 60_000, retry: false });
}

/** End a connection. Read back either way: a not-found does not prove it
 *  is gone, and a row dropped here would come back on the next read. */
export function useDisconnectApp(podId: string) {
    const source = useMcpSource();
    const cache = useQueryClient();
    const key = useKey(podId, "grants");
    return useMutation({
        mutationFn: (grantId: string) => source.disconnect(grantId),
        onSettled: () => cache.invalidateQueries({ queryKey: key }),
    });
}

/** Stop or resume what an app is told about. Read back either way. */
export function useListen(podId: string) {
    const source = useMcpSource();
    const cache = useQueryClient();
    const key = useKey(podId, "grants");
    return useMutation({
        mutationFn: ({ grantId, subscriptionId, on }: { grantId: string; subscriptionId: string; on: boolean }) => source.listen(grantId, subscriptionId, on),
        onSettled: () => cache.invalidateQueries({ queryKey: key }),
    });
}

/** The row's live line on a Lemma's Settings tab: how many apps are
 *  connected once that is read, else which apps it is for. */
export function useMcpLine(podId: string): AboutLine {
    const endpoint = useMcpEndpoint(podId);
    const grants = useGrants(podId, Boolean(endpoint.data));
    return mcpLine(grants.isSuccess ? grants.data.items.length : null);
}
