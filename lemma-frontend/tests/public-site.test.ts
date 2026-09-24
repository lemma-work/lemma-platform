import assert from "node:assert/strict";
import { test } from "node:test";
import { docsPages } from "../src/site/data/docs.ts";
import { markdownForPath } from "../src/site/markdown/pages.ts";
import { prefersMarkdown } from "../src/site/markdown/negotiate.ts";
import { llmsTxt } from "../src/site/markdown/llms-txt.ts";
import {
  PUBLIC_TEMPLATES,
  templateRunHref,
} from "../src/site/templates/catalog.ts";
import {
  legacyAddress,
  settingsFromQuery,
} from "../src/site/legacy-address.ts";
import {
  toTemplateUrl,
  toRouteTemplate,
} from "../src/site/analytics/route-template.ts";
import {
  buildVCard,
  readContactCardSpec,
} from "../src/site/share/contact-card.ts";
import { selectDesktopBuilds } from "../src/site/desktop/desktop-release.ts";

test("every documentation entry is available to AI readers at its existing URL", () => {
  assert.equal(new Set(docsPages.map((p) => p.slug)).size, docsPages.length);
  for (const page of docsPages) {
    const markdown = markdownForPath("/docs/" + page.slug);
    assert.ok(markdown?.includes("# " + page.title), page.slug);
    assert.ok(markdown?.includes(page.description), page.slug);
  }
  for (const route of ["/", "/docs", "/privacy", "/tos", "/about", "/contact"])
    assert.ok(markdownForPath(route), route);
  assert.equal(markdownForPath("/docs/not-a-guide"), null);
});

test("ordinary browser and explicit Markdown requests stay separate", () => {
  assert.equal(
    prefersMarkdown("text/html,application/xhtml+xml,*/*;q=0.8"),
    false,
  );
  assert.equal(prefersMarkdown("*/*"), false);
  assert.equal(prefersMarkdown("text/markdown"), true);
  assert.equal(prefersMarkdown("text/markdown;q=0,text/html"), false);
  assert.equal(prefersMarkdown("text/markdown;q=0.5,text/html;q=1"), false);
  assert.match(llmsTxt(), /\/openapi.json/);
});

test("template links preserve the source repository and installation entry point", () => {
  assert.equal(PUBLIC_TEMPLATES.length, 10);
  for (const template of PUBLIC_TEMPLATES)
    assert.match(
      templateRunHref(template),
      /^\/import\/github\/[\w.-]+\/[\w.-]+/,
    );
});

test("old links reach their resource rather than the default workspace", () => {
  assert.equal(
    legacyAddress(["pod", "a b", "conversations", "c"], new URLSearchParams()),
    "/t/a%20b/conversation/c",
  );
  assert.equal(
    legacyAddress(["pod", "p", "data"], new URLSearchParams({ tab: "orders" })),
    "/t/p/table/orders",
  );
  assert.equal(
    legacyAddress(
      ["pod", "p", "files"],
      new URLSearchParams({ file: "/docs/a b.md" }),
    ),
    "/t/p/file/docs/a%20b.md",
  );
  assert.equal(
    legacyAddress(
      ["organizations", "o", "settings", "billing"],
      new URLSearchParams(),
    ),
    "/t?settings=team-billing&org=o",
  );
  assert.equal(
    legacyAddress(["profile", "usage"], new URLSearchParams()),
    "/t?settings=usage",
  );
  assert.equal(legacyAddress(["not-a-page"], new URLSearchParams()), null);
  assert.equal(settingsFromQuery("anything"), null);
});

test("analytics never exports private workspace paths or link query payloads", () => {
  assert.equal(
    toRouteTemplate("/t/private-pod/file/private/report.md"),
    "/t/[[...podId]]",
  );
  assert.equal(toRouteTemplate("/d/private-share-code"), "/d/[code]");
  const url = toTemplateUrl(
    "https://example.test/t/private/conversation/secret?remixSource=private#private",
  );
  assert.ok(url && !url.includes("private") && !url.includes("secret"));
});

test("contact downloads escape user supplied fields and reject unsafe channels", () => {
  const spec = readContactCardSpec(
    new URLSearchParams({
      em: "bad\nEMAIL:injected",
      tg: "@helper",
      wa: "+15551234567",
    }),
    "Hello\nInjected",
  );
  assert.ok(spec);
  assert.equal(spec.email, null);
  const card = buildVCard(spec);
  assert.ok(card.startsWith("BEGIN:VCARD\r\n"));
  assert.ok(!card.includes("\r\nEMAIL:injected"));
  assert.match(card, /TEL;TYPE=CELL:\+15551234567/);
});

test("desktop download prefers hosted installers and never offers a local-only build", () => {
  const builds = selectDesktopBuilds([
    {
      name: "Lemma_1_aarch64-local.dmg",
      browser_download_url: "https://example.test/local",
    },
    {
      name: "Lemma_1_aarch64-online.dmg",
      browser_download_url: "https://example.test/online",
    },
  ]);
  assert.equal(builds.length, 1);
  assert.equal(builds[0].url, "https://example.test/online");
});
