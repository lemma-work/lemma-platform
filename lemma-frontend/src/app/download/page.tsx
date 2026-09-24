import { SitePage } from "@/site/chrome";
import { pageMetadata } from "@/site/metadata";
import {
  fetchLatestDesktopRelease,
  formatInstallerSize,
} from "@/site/desktop/desktop-release";
export const metadata = pageMetadata(
  "Download Lemma",
  "Connect your computer so your local agents can work with your team.",
  "/download",
);
export const revalidate = 3600;
export default async function Page() {
  const release = await fetchLatestDesktopRelease();
  return (
    <SitePage
      title="Your teammate. On your computer."
      description="Connect your computer so the agents that live on it can pick up work from your workspace."
    >
      <p>
        {release.version
          ? "Latest release: " + release.version
          : "Choose an installer from the latest release."}
      </p>
      <div className="site-grid">
        {release.builds.map((b) => (
          <a className="site-card" key={b.platform} href={b.url}>
            <h2>{b.label} ↗</h2>
            <p>{b.requirement}</p>
            <p>{formatInstallerSize(b.size)}</p>
          </a>
        ))}
      </div>
      <p>
        <a href={release.releasesUrl}>All releases and installers ↗</a>
      </p>
      <h2>Get connected</h2>
      <ol>
        <li>Install and open Lemma.</li>
        <li>Sign in to your workspace.</li>
        <li>Connect the coding agents on your computer.</li>
      </ol>
      <p>
        <a href="/docs/concepts/runtimes">Learn how runtimes work</a>
      </p>
    </SitePage>
  );
}
