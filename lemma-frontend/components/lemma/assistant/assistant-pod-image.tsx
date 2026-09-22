"use client";

import { useState } from "react";

import { usePodIdFromPath } from "@/lib/education/use-education-audience";
import { getLemmaApiBaseUrl } from "@/lib/sdk/lemma-client";
import { cn } from "@/lib/utils";

/**
 * An image an agent produced, addressed by its pod path.
 *
 * The Agent Host writes a generated image into pod files and splices
 * `![Generated image](/me/c/<date>/<slug>/agent-output/....png)` into the
 * message. Markdown rendered that as a bare `<img>` whose `src` resolved
 * against the *frontend* origin, where no `/me` route exists -- so every image
 * Codex produced arrived as a broken image icon, and the sibling link 404'd
 * beside it. The whole backend chain worked; only the last hop was missing.
 *
 * A pod path is rewritten onto the authenticated download route, which is the
 * same content the pod file browser serves. Anything already absolute (an
 * http(s) or data URI) is left exactly as the author wrote it.
 */
export function isPodFilePath(src: string | undefined): src is string {
  if (!src) return false;
  // Protocol-relative and absolute URLs are somebody else's to resolve.
  if (src.startsWith("//")) return false;
  if (/^[a-z][a-z0-9+.-]*:/i.test(src)) return false;
  return src.startsWith("/");
}

export function podFileDownloadHref(podId: string, path: string): string {
  const base = getLemmaApiBaseUrl().replace(/\/$/, "");
  return `${base}/pods/${podId}/files/download?path=${encodeURIComponent(path)}`;
}

export function podFileBrowserHref(podId: string, path: string): string {
  const parts = path.replace(/\/+$/g, "").split("/").filter(Boolean);
  const params = new URLSearchParams();
  if (parts.length > 1) params.set("folder", `/${parts.slice(0, -1).join("/")}`);
  params.set("file", path);
  return `/pod/${podId}/files?${params.toString()}`;
}

export function AssistantPodImage({
  src,
  alt,
  className,
}: {
  src?: string;
  alt?: string;
  className?: string;
}) {
  const podId = usePodIdFromPath();
  const [failed, setFailed] = useState(false);

  const resolved = isPodFilePath(src) && podId ? podFileDownloadHref(podId, src) : src;
  const filename = isPodFilePath(src) ? src.split("/").filter(Boolean).pop() : undefined;

  // No pod in the path, or the image would not load: say which file it was
  // rather than showing a broken icon with nothing to act on.
  if (!resolved || failed) {
    return (
      <span className="my-2 block text-xs text-[var(--text-secondary)]">
        {alt || "Image"}
        {filename ? ` — ${filename}` : ""}
        {isPodFilePath(src) && podId ? (
          <>
            {" "}
            <a
              className="font-medium text-[var(--action-primary)] underline-offset-4 hover:underline"
              href={podFileBrowserHref(podId, src)}
            >
              open in files
            </a>
          </>
        ) : null}
      </span>
    );
  }

  return (
    // The pod download route streams arbitrary user content behind the
    // session cookie; `next/image` would proxy it through the optimizer,
    // which does not carry that session.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={resolved}
      alt={alt || "Generated image"}
      onError={() => setFailed(true)}
      className={cn(
        "my-3 h-auto max-w-full rounded-md border border-[color:var(--row-border)]",
        className,
      )}
    />
  );
}

/**
 * A markdown link, with pod paths pointed at the pod file browser.
 *
 * The artifact writer emits `[<pod path>](<pod path>)` beside each generated
 * image. Left alone that resolved against the frontend origin and 404'd, for
 * the same reason the image did.
 */
export function AssistantMarkdownLink({
  href,
  className,
  target,
  rel,
  ...props
}: React.AnchorHTMLAttributes<HTMLAnchorElement>) {
  const podId = usePodIdFromPath();
  const isPodPath = isPodFilePath(href) && Boolean(podId);
  const resolved = isPodPath ? podFileBrowserHref(podId as string, href as string) : href;

  return (
    <a
      {...props}
      href={resolved}
      className={className}
      // A pod path stays in this tab: it is a route in this same app, and
      // opening it in a new one loses the conversation beside it.
      target={target || (isPodPath ? undefined : "_blank")}
      rel={rel || (isPodPath ? undefined : "noreferrer noopener")}
    />
  );
}
