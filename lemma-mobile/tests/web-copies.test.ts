import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { instructionsFor } from "../src/areas/files/web/page-chat";
import * as editorPage from "../src/areas/files/web/editor-page.generated";
import * as pdfPage from "../src/areas/files/web/pdf-page.generated";

/** The files area runs some of the web's own code: copies of its pure
 *  modules (`src/areas/files/web/`), and the page editor built from its
 *  sources (`webview/`). These hold both to the web, so a change there is
 *  noticed here rather than shipped half-done. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
const web = mobile + "../lemma-frontend/src/";
describe("the web's page brief", () => {
    const present = existsSync(web);
    it.runIf(present)("a page's conversation is briefed as the web briefs it", () => {
        const floating = readFileSync(web + "chat/floating-chat.tsx", "utf8");
        const brief = instructionsFor({ kind: "file", name: "/pages/Plan.md" });
        /* Every sentence of the web's brief, with the file named. */
        for (const piece of [
            "This conversation is attached to the doc `/pages/Plan.md` in this pod",
            "Make it with `pod_edit_file`",
            "A ```lemma-view fenced block is a live view of pod tables",
        ]) {
            expect(brief).toContain(piece);
        }
        const literals = [...floating.slice(floating.indexOf("function instructionsFor("), floating.indexOf("return resourceInstructions(", floating.indexOf("function instructionsFor("))).matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((match) => JSON.parse(`"${match[1]}"`) as string);
        for (const literal of literals.filter((one) => one.length > 40)) expect(brief).toContain(literal);
    });
});

/** The stamp `webview/build.mjs` writes: every input, hashed in order. */
function stamp(inputs: string[]): string {
    const hash = createHash("sha256");
    for (const file of [...inputs].sort()) {
        hash.update(file + "\0");
        hash.update(readFileSync(mobile + file));
    }
    return hash.digest("hex").slice(0, 16);
}

describe("the pages built for WebViews", () => {
    for (const [name, page] of [["the page editor", editorPage], ["the PDF reader", pdfPage]] as const) {
        it.runIf(page.INPUTS.every((file) => existsSync(mobile + file)))(`${name} is built from its sources as they are (run npm run build in webview/ if not)`, () => {
            expect(stamp(page.INPUTS)).toBe(page.STAMP);
        });
    }

    it("the editor page holds no address it could reach the API by", () => {
        expect(editorPage.HTML).not.toMatch(/api\.lemma\.work|localhost:8761/);
        expect(editorPage.HTML).not.toMatch(/Authorization|Bearer /);
    });
});
