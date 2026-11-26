import assert from "node:assert/strict";
import { test } from "node:test";

import { GET as apple } from "../src/app/.well-known/apple-app-site-association/route.ts";
import { GET as android } from "../src/app/.well-known/assetlinks.json/route.ts";
import { IOS_APP_ID } from "../src/site/app-links.ts";

/** Phones fetch these two addresses before they let the app open a lemma.work
 *  link. If either stops answering JSON, links quietly open in the browser. */

test("the Apple file answers JSON naming the app", async () => {
    const response = apple();
    assert.match(response.headers.get("content-type") ?? "", /^application\/json/);
    const body = await response.json();
    assert.deepEqual(body.applinks.details[0].appIDs, [IOS_APP_ID]);
    assert.ok(body.applinks.details[0].components.some((one: { "/": string }) => one["/"] === "/l/*"));
});

test("the Android file answers JSON, a list", async () => {
    const response = android();
    assert.match(response.headers.get("content-type") ?? "", /^application\/json/);
    assert.ok(Array.isArray(await response.json()));
});
