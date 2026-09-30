import test from "node:test";
import assert from "node:assert/strict";
import { docTitle, fileKind } from "../src/library/doc-title.ts";

test("a slug becomes a sentence-case title, without its extension", () => {
    assert.equal(docTitle("upworkanalysis.md"), "Upworkanalysis");
    assert.equal(docTitle("weekly_report.md"), "Weekly report");
    assert.equal(docTitle("Project brief.md"), "Project brief");
    assert.equal(docTitle("upworkAnalysis.md"), "Upwork Analysis");
    assert.equal(docTitle("/pages/q1-vendors.md"), "Q1 vendors");
});

test("a date in the name is said as a date, after the title", () => {
    assert.equal(docTitle("lemma-content-plan-2026-08-31.md"), "Lemma content plan · 31 Aug 2026");
    assert.equal(docTitle("2026-09-30-standup.md"), "Standup · 30 Sept 2026");
    assert.equal(docTitle("20260930.md"), "30 Sept 2026");
});

test("acronyms stay capitals, and hidden or empty names are left alone", () => {
    assert.equal(docTitle("gtm-plan.md"), "GTM plan");
    assert.equal(docTitle("api-notes.md"), "API notes");
    assert.equal(docTitle(".env"), ".env");
    assert.equal(docTitle("meeting-with-Priya.md"), "Meeting with Priya");
    assert.equal(docTitle("/pages/Plan/Untitled-munpqovy.md"), "Untitled");
    assert.equal(docTitle("Untitled mf3k2a9x.md"), "Untitled");
});

test("the kind line is a word, not a MIME type, unless there is a description", () => {
    assert.equal(fileKind("a.md", "text/markdown"), "Page");
    assert.equal(fileKind("a.pdf", "application/pdf"), "PDF");
    assert.equal(fileKind("photo.PNG", "image/png"), "Image");
    assert.equal(fileKind("a.md", "Weekly progress and next steps"), "Weekly progress and next steps");
    assert.equal(fileKind("thing.xyz", ""), "XYZ");
});
