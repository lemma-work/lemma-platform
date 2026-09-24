import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
export default async function Page({
  params,
}: {
  params: Promise<{ invitationId: string }>;
}) {
  return (
    <SitePage title="Decline invitation">
      <ActionHost
        action="invite"
        decision="reject"
        invitationId={(await params).invitationId}
      />
    </SitePage>
  );
}
