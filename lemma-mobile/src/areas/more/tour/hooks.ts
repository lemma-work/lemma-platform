import { useQuery } from "@tanstack/react-query";
import { useCacheKey } from "../../../data/hooks";
import { moreKey, useMoreSource } from "../hooks";

export function useTourSource() {
    return useMoreSource().tour;
}

/** Who the tour is for, and how new the account is. */
export function useTourAccount() {
    const source = useTourSource();
    const key = useCacheKey();
    return useQuery({ queryKey: moreKey(key, "tour", "account"), queryFn: () => source.account(), staleTime: 30 * 60_000, retry: false });
}
