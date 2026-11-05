import test from "node:test";
import assert from "node:assert/strict";
import { attachmentFolder, attachmentSaid, carriedFiles, dragHasFiles, enterSends, uniqueNames, uploadProgress } from "../src/channels/compose-model.ts";
import { firstUnread, newSince, sayNewLines, unreadBeforeHeld } from "../src/channels/timeline-model.ts";
import { answeringNews } from "../src/channels/live.ts";

/** What the composer and the timeline decide without a page. */

const priya = { kind: "MEMBER" as const, id: "user-priya", name: "Priya" };
const me = { kind: "MEMBER" as const, id: "me", name: "Dana" };

test("a line's files go in a folder of the line's own, and two of one name in a line are kept apart", () => {
    assert.equal(attachmentFolder("c1", "cm-1"), "/me/channels/c1/cm-1");
    assert.notEqual(attachmentFolder("c1", "cm-1"), attachmentFolder("c1", "cm-2"), "two lines never share a folder");
    assert.deepEqual(uniqueNames(["image.png", "image.png", "Image.PNG", "notes", "notes"]), ["image.png", "image (2).png", "Image (3).PNG", "notes", "notes (2)"]);
});

test("Enter sends, Shift+Enter breaks the line, and on a touch screen Enter is a new line", () => {
    assert.equal(enterSends({ key: "Enter", shiftKey: false, isComposing: false }, false), true);
    assert.equal(enterSends({ key: "Enter", shiftKey: true, isComposing: false }, false), false);
    assert.equal(enterSends({ key: "Enter", shiftKey: false, isComposing: true }, false), false, "a composition being typed is not a send");
    assert.equal(enterSends({ key: "Enter", shiftKey: false, isComposing: false }, true), false);
    assert.equal(enterSends({ key: "a", shiftKey: false, isComposing: false }, false), false);
});

test("a paste or a drop carries files, and only a drag of files lights the composer", () => {
    const file = new File(["x"], "a.txt");
    assert.deepEqual(carriedFiles({ files: [file] }), [file]);
    assert.deepEqual(carriedFiles(null), []);
    assert.equal(dragHasFiles(["text/plain", "Files"]), true);
    assert.equal(dragHasFiles(["text/plain"]), false);
    assert.equal(dragHasFiles(undefined), false);
});

test("an upload says how it is getting on", () => {
    assert.equal(uploadProgress(["uploaded", "uploading", "waiting"]), "Uploading 2 of 3…");
    assert.equal(uploadProgress(["uploading"]), "Uploading the file…");
    assert.equal(uploadProgress(["uploaded", "failed"]), null);
    assert.equal(attachmentSaid("failed"), "didn’t upload");
    assert.equal(attachmentSaid("waiting"), "");
});

test("the unread lines start at the first line from somebody else past the read position", () => {
    const lines = [{ id: "a", seq: 4, sender: priya }, { id: "b", seq: 5, sender: me }, { id: "c", seq: 6, sender: priya }];
    assert.equal(firstUnread(lines, 4, "me"), "c", "the reader's own line is not news");
    assert.equal(firstUnread(lines, 6, "me"), null);
    assert.equal(unreadBeforeHeld(lines, 1, true), true, "unread lines older than every page held");
    assert.equal(unreadBeforeHeld(lines, 3, true), false);
    assert.equal(unreadBeforeHeld(lines, 1, false), false, "nothing older to read");
});

test("lines that came in while the reader read back are counted on the pill", () => {
    const lines = [{ seq: 4, sender: priya }, { seq: 5, sender: me }, { seq: 6, sender: priya }, { seq: 7, sender: priya }];
    assert.equal(newSince(lines, 4, "me"), 2);
    assert.equal(sayNewLines(1), "1 new line");
    assert.equal(sayNewLines(2), "2 new lines");
});

test("an answer starting is news once; one already going is not", () => {
    const going = [{ messageId: "l1", agentId: "lem", text: "so far" }];
    assert.deepEqual(answeringNews([], going).map((one) => one.agentId), ["lem"]);
    assert.deepEqual(answeringNews(going, [{ ...going[0], text: "so far, more" }]), []);
});
