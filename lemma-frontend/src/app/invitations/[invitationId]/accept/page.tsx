import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
export default async function Page({
  params,
}: {
  params: Promise<{ invitationId: string }>;
}) {
  return (
    <SitePage title="Join this organization">
      <ActionHost
        action="invite"
        decision="accept"
        invitationId={(await params).invitationId}
      />
    </SitePage>
  );
}
