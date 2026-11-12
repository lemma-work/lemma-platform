import { useLocalSearchParams } from "expo-router";
import { ConnectScreen } from "../../../../../../areas/admin/works-with/screens";

/** Let another Lemma ask this one, or change what one reads (`areas/admin/works-with`). */
export default function ConnectLemma() {
    const params = useLocalSearchParams<{ podId: string; asker?: string }>();
    return <ConnectScreen podId={String(params.podId)} asker={params.asker ? String(params.asker) : undefined} />;
}
