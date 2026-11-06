import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { createRequire } from "node:module";
import type * as TS from "typescript";

/* The words a person never reads in the Lemma frame. A Lemma is not a pod,
   a teammate or a bot; what it owns is not a goal or a mission (and never
   "an outcome": that word stays in the code); and a chat is a chat, never a
   channel or someone's "line". The phone app holds the same list in its
   tests, so the two say things the same way. */
const NEVER = /\b(teammate|pod|bot|channel|goal|mission|outcome)s?\b/i;
const NEVER_A_CHAT = /\b(your|own|my|private) line\b|\bline on\b/i;

/* Attributes that hold code, not words. */
const NOT_SAID = new Set(["className", "key", "href", "id", "role", "type", "name", "htmlFor", "rel", "target", "method", "inputMode", "autoComplete"]);

const ts = createRequire(import.meta.url)("typescript") as typeof TS;
const DIR = new URL("../src/outcomes/", import.meta.url);

/** Everything a component in the frame can put in front of a person: text
 *  between tags, and every string or template that reads like words (two
 *  words or more) outside an attribute that holds code. Strings written to a
 *  Lemma rather than shown to a person live in `data.ts`, which is left out. */
function saidIn(file: string): string[] {
    const source = ts.createSourceFile(file, readFileSync(new URL(file, DIR), "utf8"), ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
    const said: string[] = [];
    const inCode = (node: TS.Node): boolean => {
        for (let at: TS.Node | undefined = node.parent; at; at = at.parent) {
            if (ts.isJsxAttribute(at)) {
                const name = at.name.getText(source);
                return NOT_SAID.has(name) || name.startsWith("data-") || name.startsWith("aria-controls");
            }
            if (ts.isImportDeclaration(at) || ts.isExportDeclaration(at)) return true;
        }
        return false;
    };
    const visit = (node: TS.Node) => {
        if (ts.isJsxText(node)) {
            const text = node.text.replace(/\s+/g, " ").trim();
            if (text) said.push(text);
        } else if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && /[a-z]{2,} [a-z]/i.test(node.text) && !inCode(node)) {
            said.push(node.text);
        } else if (ts.isTemplateExpression(node) && !inCode(node)) {
            const text = [node.head.text, ...node.templateSpans.map((span) => span.literal.text)].join("…");
            if (/[a-z]{2,} [a-z]/i.test(text)) said.push(text);
        }
        ts.forEachChild(node, visit);
    };
    visit(source);
    return said;
}

const FILES = readdirSync(DIR).filter((file) => /\.tsx?$/.test(file) && file !== "data.ts");

test("the frame never calls a Lemma a pod, a teammate or a bot, or a chat a channel or a line", () => {
    const wrong = FILES.flatMap((file) => saidIn(file).filter((text) => NEVER.test(text) || NEVER_A_CHAT.test(text)).map((text) => `${file}: ${text}`));
    assert.deepEqual(wrong, []);
});

test("the scan reads words where they are said", () => {
    /* A check that finds nothing should be finding something to look at. */
    const said = FILES.flatMap(saidIn);
    assert.ok(said.length > 200, `only ${said.length} strings read`);
    assert.ok(said.some((text) => text.includes("Nothing it owns yet")));
});
