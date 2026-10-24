import { useLocalSearchParams } from "expo-router";
import { InstallScreen } from "../../../../../areas/more/install/screens";

/** A GitHub repository, and installing it on a Lemma. The web's own path,
 *  so `lemma://import/github/{owner}/{repo}` opens it. */
export default function ImportFromGitHub() {
    const params = useLocalSearchParams<{ owner: string; repo: string }>();
    return <InstallScreen owner={String(params.owner ?? "")} repo={String(params.repo ?? "")} />;
}
