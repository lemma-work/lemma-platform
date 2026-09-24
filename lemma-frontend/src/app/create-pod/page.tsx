import { redirect } from "next/navigation";
import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
export default async function Page({
  searchParams,
}: {
  searchParams: Promise<{ remixSource?: string }>;
}) {
  const { remixSource } = await searchParams;
  if (!remixSource) redirect("/t?hire=1");
  return (
    <SitePage title="Create a workspace for this remix">
      <ActionHost action="remix" source={remixSource} />
    </SitePage>
  );
}
