import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ source?: string }>;
}) {
  return (
    <SitePage
      title="Make it yours"
      description="Bring an app to a Lemma and build on it together."
    >
      <ActionHost action="remix" source={(await searchParams).source} />
    </SitePage>
  );
}
