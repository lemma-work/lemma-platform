import { useLocalSearchParams } from "expo-router";
import { DetailsScreen } from "../../../../../areas/chats/details";
import type { DetailsTab } from "../../../../../areas/chats/routes";

/** A chat's details: how you hear of it, what it is, who is in it, who
 *  answers, where it is mirrored from, its settings (PARITY.md, E). */
export default function GroupDetails() {
    const params = useLocalSearchParams<{ podId: string; channel: string; tab?: string }>();
    return <DetailsScreen podId={String(params.podId)} channelId={String(params.channel)} tab={(params.tab as DetailsTab | undefined) ?? null} />;
}
