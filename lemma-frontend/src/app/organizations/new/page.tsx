import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
export default function Page() {
  return (
    <SitePage title="Create your organization">
      <ActionHost action="organization" />
    </SitePage>
  );
}
