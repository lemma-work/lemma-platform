import test from "node:test";
import assert from "node:assert/strict";
import { embeddedUrl, imageSource, isPodReference, rewriteHtml } from "../src/app/d/[code]/embedded.ts";

const via = (reference: string) => embeddedUrl("c0de", reference);

test("only the pod's own references go through the link", () => {
    assert.ok(isPodReference("Plan-files/chart.png"));
    assert.ok(isPodReference("/pages/Plan-files/chart.png"));
    assert.ok(isPodReference("<Plan files/a.png>"));

    // The web, inline bytes and in-page anchors are the browser's business.
    for (const reference of ["https://example.com/a.png", "//cdn.example.com/a.js", "data:image/png;base64,AA", "#top", "mailto:a@b.c", ""]) {
        assert.equal(isPodReference(reference), false, reference);
    }
});

test("the reference travels as written, so the API decides what it names", () => {
    assert.equal(via("Plan files/a b.png"), "/d/c0de/a?ref=Plan%20files%2Fa%20b.png");
});

test("a markdown picture: through the link, from the web as itself, or not at all", () => {
    assert.equal(imageSource("c0de", "chart.png"), via("chart.png"));
    assert.equal(imageSource("c0de", "https://example.com/x.png"), "https://example.com/x.png");
    // Plain http would be mixed content, and anything else is not a picture.
    assert.equal(imageSource("c0de", "http://example.com/x.png"), null);
    assert.equal(imageSource("c0de", "javascript:alert(1)"), null);
});

test("what a page loads is rewritten, and its links are not", () => {
    const page = [
        '<link rel="stylesheet" href="assets/style.css">',
        '<link rel="canonical" href="other.html">',
        "<img src='a.png' srcset=\"a-2x.png 2x, https://cdn.example.com/a-3x.png 3x\">",
        '<video src=clip.mp4 poster="poster.jpg"></video>',
        '<a href="next.html">Next</a>',
        '<img src="https://example.com/remote.png">',
    ].join("\n");
    const out = rewriteHtml(page, "c0de");

    assert.ok(out.includes('href="' + via("assets/style.css") + '"'));
    assert.ok(out.includes('href="other.html"'), "a non-loading link is untouched");
    assert.ok(out.includes('src="' + via("a.png") + '"'));
    assert.ok(out.includes(via("a-2x.png") + " 2x, https://cdn.example.com/a-3x.png 3x"));
    assert.ok(out.includes('src="' + via("clip.mp4") + '"'));
    assert.ok(out.includes('poster="' + via("poster.jpg") + '"'));
    assert.ok(out.includes('<a href="next.html">'), "a link to another document stays a link");
    assert.ok(out.includes('src="https://example.com/remote.png"'));
});

test("CSS in a style element and a style attribute", () => {
    const page = "<style>@import 'extra.css'; body { background: url(\"bg.jpg\") } .x { background: url(https://e.com/y.png) }</style>"
        + "<div style=\"background-image: url(texture.png)\"></div>";
    const out = rewriteHtml(page, "c0de");
    assert.ok(out.includes("@import '" + via("extra.css") + "'"));
    assert.ok(out.includes('url("' + via("bg.jpg") + '")'));
    assert.ok(out.includes("url(https://e.com/y.png)"));
    assert.ok(out.includes("url(" + via("texture.png") + ")"));
});
