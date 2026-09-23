import test from "node:test";
import assert from "node:assert/strict";
import {
    attachmentKey,
    canSend,
    contentFor,
    describeSize,
    markAttachment,
    NOTHING_TYPED,
    toAttachments,
    tooLarge,
    withReferences,
    isAlreadyUploaded,
    MAX_BYTES,
    type Attachment,
} from "../src/thread/attachments.ts";

function file(name: string, size = 10): File {
    return new File([new Uint8Array(size)], name);
}

test("the message names attachments using the agent file contract", () => {
    // Character for character. Two surfaces of one product performing the same
    // act must reach the agent as the same prompt.
    const content = withReferences("have a look", [
        { name: "report.pdf", path: "/me/c/2026-09-18/quiet-fox/report.pdf" },
        { name: "notes.md", path: "/me/c/2026-09-18/quiet-fox/notes.md" },
    ]);

    assert.equal(
        content,
        "have a look\n\nPersonal files available to this run:\n" +
            "- report.pdf: /me/c/2026-09-18/quiet-fox/report.pdf\n" +
            "- notes.md: /me/c/2026-09-18/quiet-fox/notes.md",
    );
});

test("a file with no name of its own is named by its path", () => {
    assert.match(withReferences("x", [{ name: null, path: "/me/c/d/s/report.pdf" }]), /- report\.pdf: /);
    assert.match(withReferences("x", [{ path: "/me/c/d/s/report.pdf" }]), /- report\.pdf: /);
});

test("nothing attached leaves the message exactly as typed", () => {
    assert.equal(withReferences("just a question", []), "just a question");
});

test("a file with no message still says something", () => {
    // An agent handed an empty string has been told nothing. "Here, look at
    // this" is a real thing to send.
    assert.equal(contentFor("   ", toAttachments([file("a.pdf")])), NOTHING_TYPED);
    assert.equal(contentFor("look at this", toAttachments([file("a.pdf")])), "look at this");
    assert.equal(contentFor("  spaced  ", []), "spaced");
    assert.equal(contentFor("   ", []), "");
});

test("send is allowed by either half", () => {
    assert.equal(canSend("", []), false);
    assert.equal(canSend("   ", []), false);
    assert.equal(canSend("hello", []), true);
    assert.equal(canSend("", toAttachments([file("a.pdf")])), true);
});

test("two files with the same name stay two files", () => {
    // Keyed on name alone, removing one chip would remove both — and people do
    // attach the same file twice by accident and then take one off.
    const [first, second] = toAttachments([file("report.pdf"), file("report.pdf")]);

    assert.notEqual(first.key, second.key);
    assert.notEqual(attachmentKey(file("a.pdf")), attachmentKey(file("a.pdf")));
});

test("a status change touches one attachment and copies the rest", () => {
    const attachments = toAttachments([file("a.pdf"), file("b.pdf")]);

    const next = markAttachment(attachments, attachments[1].key, { status: "failed", error: "nope" });

    assert.equal(next[0], attachments[0]);
    assert.deepEqual(
        next[1],
        { ...attachments[1], status: "failed", error: "nope" },
    );
    assert.equal(attachments[1].status, "queued", "the original was mutated");
});

test("an attachment starts queued rather than pretending to have landed", () => {
    assert.deepEqual(toAttachments([file("a.pdf")]).map((one: Attachment) => one.status), ["queued"]);
});

test("a file too large to send is refused before the upload, not after it", () => {
    assert.equal(tooLarge(file("small.pdf", 1024)), false);
    assert.equal(tooLarge(file("huge.bin", MAX_BYTES + 1)), true);
    assert.equal(tooLarge(file("exact.bin", MAX_BYTES)), false);
});

test("sizes read the way a person would say them", () => {
    assert.equal(describeSize(512), "512 B");
    assert.equal(describeSize(2048), "2 KB");
    assert.equal(describeSize(1536 * 1024), "1.5 MB");
    assert.equal(describeSize(40 * 1024 * 1024), "40 MB");
});

test("a file already in the pod is not put there a second time", () => {
    // It can be up there without the message having gone: the upload succeeds,
    // the send after it fails, and the chip comes back to the composer. A retry
    // that uploaded again would leave two copies of one file, and the second
    // would take the name.
    const [one] = toAttachments([file("report.pdf")]);

    assert.equal(isAlreadyUploaded(one), false);
    assert.equal(isAlreadyUploaded({ ...one, status: "uploaded", path: "/me/c/d/s/report.pdf" }), true);

    // "uploaded" without a path is not something that can be referenced, so it
    // is not something that can be skipped either.
    assert.equal(isAlreadyUploaded({ ...one, status: "uploaded" }), false);
    assert.equal(isAlreadyUploaded({ ...one, status: "uploaded", path: "" }), false);
    assert.equal(isAlreadyUploaded({ ...one, status: "failed", path: "/me/x.pdf" }), false);
});
