import test from "node:test";
import assert from "node:assert/strict";
import {
    attachmentKey,
    canSend,
    offered,
    contentFor,
    describeSize,
    markAttachment,
    NOTHING_TYPED,
    toAttachments,
    tooLarge,
    withReferences,
    isAlreadyUploaded,
    namePasted,
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

test("what a drop hands over is split by the ceiling, in one sentence", () => {
    // The composer and the box on Home are the same act on two surfaces, so
    // they refuse the same file with the same words — a second copy of this
    // rule is a second ceiling, and the one that drifts is the one people hit.
    const small = file("small.pdf", 1024);
    const huge = file("huge.bin", MAX_BYTES + 1);
    const alsoHuge = file("dump.zip", MAX_BYTES * 2);

    assert.deepEqual(offered([small]), { take: [small], refused: null });
    assert.deepEqual(offered([huge]), { take: [], refused: "huge.bin is too large to attach (100 MB)." });
    assert.deepEqual(offered([small, huge]), { take: [small], refused: "huge.bin is too large to attach (100 MB)." });
    assert.deepEqual(offered([huge, alsoHuge]), { take: [], refused: "2 files are too large to attach." });
    assert.deepEqual(offered([]), { take: [], refused: null });
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

test("two pasted images are two names, not one", () => {
    // Every clipboard image is called `image.png`. Two pastes in one message
    // were therefore one name, and a folder that already held an `image.png`
    // had nowhere to put either.
    const at = new Date(2026, 9, 17, 6, 41, 58);
    const [first, second] = namePasted([file("image.png"), file("image.png")], at);

    assert.equal(first.name, "pasted-20261017-064158.png");
    assert.equal(second.name, "pasted-20261017-064158-2.png");
});

test("a second paste in the same second is still a different name", () => {
    // The two arrive as two paste events, so the count has to outlive one call.
    const at = new Date(2026, 9, 17, 6, 41, 59);
    const [one] = namePasted([file("image.png")], at);
    const [two] = namePasted([file("image.png")], at);

    assert.equal(one.name, "pasted-20261017-064159.png");
    assert.equal(two.name, "pasted-20261017-064159-2.png");
});

test("a paste of two then a paste of one in the same second gives three names", () => {
    // The count is of files named, not of pastes: counting pastes would hand
    // the third file the second one's name.
    const at = new Date(2026, 9, 17, 6, 41, 57);
    const firstPaste = namePasted([file("image.png"), file("image.png")], at);
    const [third] = namePasted([file("image.png")], at);

    assert.deepEqual(
        [...firstPaste.map((one) => one.name), third.name],
        ["pasted-20261017-064157.png", "pasted-20261017-064157-2.png", "pasted-20261017-064157-3.png"],
    );
});

test("a pasted file keeps a readable extension, from its name or its type", () => {
    const at = (second: number) => new Date(2026, 9, 17, 6, 42, second);

    assert.equal(
        namePasted([file("Screenshot 2026-10-17.PNG")], at(0))[0].name,
        "pasted-20261017-064200.png",
    );
    // A clipboard image often arrives with no extension at all; without one it
    // cannot be read back as an image by anything.
    assert.equal(
        namePasted([new File([new Uint8Array(4)], "image", { type: "image/jpeg" })], at(1))[0].name,
        "pasted-20261017-064201.jpg",
    );
    assert.equal(
        namePasted([new File([new Uint8Array(4)], "blob", { type: "application/pdf" })], at(2))[0].name,
        "pasted-20261017-064202.pdf",
    );
});
