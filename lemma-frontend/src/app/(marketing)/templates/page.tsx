import { PluginStore, STORE_INTRO, STORE_TITLE } from "@/site/plugins/store/store";
import { pageMetadata } from "@/site/metadata";

export const metadata = pageMetadata("Plugins", `${STORE_TITLE}. ${STORE_INTRO}`, "/templates");

export default function Page() {
    return <PluginStore />;
}
