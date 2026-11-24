import test from "node:test";
import assert from "node:assert/strict";
import { sampleChannelApi } from "../src/channels/sample-api.ts";
import { sampleReportApi } from "../src/reports/sample-api.ts";

/** Reporting and blocking in the sample workspace: answered from fixtures,
 *  so what the dialogs say once they succeed, and a blocked person's lines,
 *  can be walked with no session. */

const TOMAS = { kind: "CONTACT", id: "contact-tomas" } as const;

test("a report in the sample is taken", async () => {
    await sampleReportApi("sample").report({ kind: "line", channelId: "sample-launch", lineId: "l-3" }, "SPAM", "");
});

test("blocking somebody in the sample hides their lines from the reader until it is lifted", async () => {
    const reports = sampleReportApi("sample");
    const channels = sampleChannelApi("sample");
    const tomas = async () => (await channels.lines("sample-launch")).items.find((line) => line.id === "l-3");

    await reports.block(TOMAS);
    const hidden = await tomas();
    assert.equal(hidden?.blocked, true);
    assert.equal(hidden?.text, null);
    // Everybody else's lines read as before, the Lemma's answer to him too.
    const answer = (await channels.lines("sample-launch")).items.find((line) => line.id === "l-4");
    assert.equal(answer?.blocked, undefined);
    assert.match(answer?.text ?? "", /CSV export/);

    await reports.unblock(TOMAS);
    const back = await tomas();
    assert.equal(back?.blocked, undefined);
    assert.equal(back?.text, "Does the beta include the export to CSV?");
    await assert.rejects(reports.unblock(TOMAS), { statusCode: 404 });
});
