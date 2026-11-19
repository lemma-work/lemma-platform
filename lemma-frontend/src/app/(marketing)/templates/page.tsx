import { LISTING_INTRO, LISTING_TITLE, PluginListing } from "@/site/plugins/listing/listing";
import { pageMetadata } from "@/site/metadata";

export const metadata = pageMetadata("Plugins", `${LISTING_TITLE}. ${LISTING_INTRO}`, "/templates");

export default function Page() {
    return <PluginListing />;
}
