import test from "node:test";
import assert from "node:assert/strict";
import { isInlineSafe, sharedFileHeaders } from "../src/site/shared-file-headers.ts";

/** A shared file, served back through this app's own origin. */

const upstream = (type: string, disposition?: string) =>
    new Headers({ "content-type": type, "content-length": "10", ...(disposition ? { "content-disposition": disposition } : {}) });

test("an uploaded page is never rendered on this origin", () => {
    // The API answers HTML and SVG as attachments; the proxy used to drop that.
    for (const type of ["text/html; charset=utf-8", "image/svg+xml", "application/xhtml+xml", "application/octet-stream"]) {
        const headers = sharedFileHeaders(upstream(type, 'attachment; filename="x"'), false);
        assert.match(headers.get("content-disposition") ?? "", /^attachment/, type);
        assert.equal(headers.get("x-content-type-options"), "nosniff");
        assert.match(headers.get("content-security-policy") ?? "", /^sandbox/);
    }
    // Even when the API said nothing, a type that can script is not inline.
    assert.match(sharedFileHeaders(upstream("text/html"), false).get("content-disposition") ?? "", /^attachment/);
    // And an API that said inline for one is overruled.
    assert.match(
        sharedFileHeaders(upstream("text/html", 'inline; filename="x.html"'), false).get("content-disposition") ?? "",
        /^attachment; filename="x.html"/,
    );
});

test("pictures, media and PDFs still show in place", () => {
    const image = sharedFileHeaders(upstream("image/png", 'inline; filename="a.png"'), false);
    assert.equal(image.get("content-disposition"), 'inline; filename="a.png"');
    assert.equal(image.get("x-content-type-options"), "nosniff");
    assert.match(image.get("content-security-policy") ?? "", /^sandbox/);
    const pdf = sharedFileHeaders(upstream("application/pdf", 'inline; filename="a.pdf"'), false);
    assert.equal(pdf.get("content-disposition"), 'inline; filename="a.pdf"');
    // Chrome's viewer refuses to run in a sandboxed document.
    assert.equal(pdf.get("content-security-policy"), null);
});

test("save turns anything into a download and keeps the name", () => {
    const saved = sharedFileHeaders(upstream("image/png", 'inline; filename="a.png"'), true);
    assert.equal(saved.get("content-disposition"), 'attachment; filename="a.png"');
    assert.equal(sharedFileHeaders(upstream("application/pdf"), true).get("content-disposition"), "attachment");
});

test("the inline allowlist is the API's", () => {
    assert.ok(isInlineSafe("image/jpeg"));
    assert.ok(isInlineSafe("text/plain; charset=utf-8"));
    assert.ok(!isInlineSafe("image/svg+xml"));
    assert.ok(!isInlineSafe("text/html"));
    assert.ok(!isInlineSafe("text/xml"));
});
