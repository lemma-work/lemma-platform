import { termsOfService as document } from "@/site/data/legal";
import { legalDocumentToMarkdown } from "@/site/markdown/render";
import { SitePage } from "@/site/chrome";
import { Prose } from "@/site/prose";
import { pageMetadata } from "@/site/metadata";
export const metadata = pageMetadata(
  document.title,
  document.description,
  "/tos",
);
export default function Page() {
  return (
    <SitePage title={document.title} description={document.description}>
      <Prose>
        {legalDocumentToMarkdown(document).split("\n\n").slice(2).join("\n\n")}
      </Prose>
    </SitePage>
  );
}
