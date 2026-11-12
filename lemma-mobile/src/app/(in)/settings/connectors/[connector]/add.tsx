import { useLocalSearchParams } from "expo-router";
import { AddOwnScreen } from "../../../../../areas/admin/connectors/screens";

/** Adding a server, an API or a database of the organization's own. */
export default function AddOwnRoute() {
    const params = useLocalSearchParams<{ connector: string; kind?: string }>();
    return <AddOwnScreen connectorId={String(params.connector)} kind={String(params.kind ?? "")} />;
}
