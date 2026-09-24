import { SitePage } from "@/site/chrome";
import { ActionHost } from "@/site/action-host";
export default function Page() {
  return (
    <SitePage title="Sign out">
      <ActionHost action="logout" />
    </SitePage>
  );
}
