import { useLocalSearchParams } from "expo-router";
import { ConnectorScreen } from "../../../../../areas/admin/connectors/screens";

/** One of the organization's connectors: its accounts and installs. */
export default function ConnectorRoute() {
    const params = useLocalSearchParams<{ connector: string }>();
    return <ConnectorScreen connectorId={String(params.connector)} />;
}
