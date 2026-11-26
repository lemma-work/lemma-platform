import { useLocalSearchParams } from "expo-router";
import { EditInstallScreen } from "../../../../../../../areas/admin/connectors/screens";

/** Changing an install's name and settings. */
export default function EditInstallRoute() {
    const params = useLocalSearchParams<{ connector: string; install: string }>();
    return <EditInstallScreen connectorId={String(params.connector)} installId={String(params.install)} />;
}
