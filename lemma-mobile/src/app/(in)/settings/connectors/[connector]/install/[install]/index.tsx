import { useLocalSearchParams } from "expo-router";
import { InstallScreen } from "../../../../../../../areas/admin/connectors/screens";

/** One of the organization's installs of a connector. */
export default function InstallRoute() {
    const params = useLocalSearchParams<{ connector: string; install: string }>();
    return <InstallScreen connectorId={String(params.connector)} installId={String(params.install)} />;
}
