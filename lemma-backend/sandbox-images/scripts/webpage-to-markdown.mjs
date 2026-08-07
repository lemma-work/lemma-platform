#!/usr/bin/env node
import fs from "node:fs";
import { JSDOM } from "jsdom";
import { Readability } from "@mozilla/readability";
import TurndownService from "turndown";

function usage() {
  console.error(
    "Usage: webpage-to-markdown <html-file> [--url <url>] [--title <title>]",
  );
}

function parseArgs(argv) {
  const args = {
    htmlFile: null,
    url: "",
    title: "",
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--url") {
      args.url = argv[++i] ?? "";
    } else if (arg === "--title") {
      args.title = argv[++i] ?? "";
    } else if (arg === "-h" || arg === "--help") {
      usage();
      process.exit(0);
    } else if (!args.htmlFile) {
      args.htmlFile = arg;
    } else {
      console.error(`Unexpected argument: ${arg}`);
      usage();
      process.exit(2);
    }
  }

  if (!args.htmlFile) {
    usage();
    process.exit(2);
  }

  return args;
}

function compactBlankLines(markdown) {
  return markdown
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const args = parseArgs(process.argv.slice(2));
const html = fs.readFileSync(args.htmlFile, "utf8");
const dom = new JSDOM(html, { url: args.url || "https://example.invalid/" });
const document = dom.window.document;

for (const selector of [
  "script",
  "style",
  "noscript",
  "template",
  "svg",
  "canvas",
  "iframe",
  "form",
]) {
  document.querySelectorAll(selector).forEach((node) => node.remove());
}

const article = new Readability(document.cloneNode(true)).parse();
const title =
  article?.title?.trim() ||
  args.title.trim() ||
  document.title?.trim() ||
  args.url ||
  "Captured page";
const contentHtml =
  article?.content ||
  document.querySelector("article")?.innerHTML ||
  document.querySelector("main")?.innerHTML ||
  document.body?.innerHTML ||
  html;

const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  bulletListMarker: "-",
});

turndown.remove(["script", "style", "noscript", "template", "svg", "canvas"]);
turndown.addRule("cleanLinks", {
  filter: "a",
  replacement(content, node) {
    const href = node.getAttribute("href");
    const text = content.trim();
    if (!href) return text;
    try {
      const absolute = new URL(href, args.url || "https://example.invalid/");
      return text ? `[${text}](${absolute.href})` : absolute.href;
    } catch {
      return text;
    }
  },
});

const body = compactBlankLines(turndown.turndown(contentHtml));
const metadata = [
  `# ${title}`,
  "",
  args.url ? `Source: ${args.url}` : "",
  `Captured: ${new Date().toISOString()}`,
  "",
]
  .filter((line, index, arr) => line || arr[index - 1] !== "")
  .join("\n");

process.stdout.write(`${metadata}${body ? `\n${body}\n` : "\n"}`);
