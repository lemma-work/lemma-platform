import test from "node:test";
import assert from "node:assert/strict";
import { LOCATION_MESSAGE_TYPE, OPEN_MESSAGE_TYPE, OPEN_RESULT_MESSAGE_TYPE, acknowledgeOpen, appPath, appUrlAt, readLocation, readOpen, tellLocation } from "../src/thread/location-bridge.ts";

/** A framed app says where it is and asks for another app. What it says ends
 *  up in the address bar, so the bridge takes it from one window only -- the
 *  frame of the app in front -- and only in the one spelling a path has. */

/** A window, as far as the bridge looks at one: something to compare and to
 *  post to. */
function frame(): { view: Window; told: { message: unknown; origin: string }[] } {
    const told: { message: unknown; origin: string }[] = [];
    const view = { postMessage: (message: unknown, origin: string) => { told.push({ message, origin }); } } as unknown as Window;
    return { view, told };
}

function message(data: unknown, source: Window, origin = "https://people.apps.example"): MessageEvent {
    return { data, source, origin } as unknown as MessageEvent;
}

test("a path inside an app has one spelling", () => {
    assert.equal(appPath("/"), "/");
    assert.equal(appPath("/1042"), "/1042");
    assert.equal(appPath("/1042/"), "/1042");
    assert.equal(appPath("/people/1042/notes"), "/people/1042/notes");
    for (const not of ["", "1042", "//evil.example", "/a//b", "/../x", "/a/./b", "/a?b=1", "/a#b", "/a b", "/a\\b", 7, null, undefined, "/" + "a".repeat(600)]) {
        assert.equal(appPath(not), null, String(not));
    }
});

test("the app in front says where it moved", () => {
    const { view } = frame();
    assert.equal(readLocation(message({ type: LOCATION_MESSAGE_TYPE, path: "/1042/" }, view), view), "/1042");
    assert.equal(readLocation(message({ type: LOCATION_MESSAGE_TYPE, path: "/" }, view), view), "/");
});

test("nobody else does, and nothing else is a move", () => {
    const { view } = frame();
    const other = frame().view;
    assert.equal(readLocation(message({ type: LOCATION_MESSAGE_TYPE, path: "/1042" }, other), view), null, "another frame");
    assert.equal(readLocation(message({ type: LOCATION_MESSAGE_TYPE, path: "/1042" }, view), null), null, "no app in front");
    assert.equal(readLocation(message({ type: OPEN_MESSAGE_TYPE, app: "deals", path: "/88" }, view), view), null, "a request for another app");
    assert.equal(readLocation(message({ type: LOCATION_MESSAGE_TYPE, path: "//evil.example" }, view), view), null, "not a path");
    assert.equal(readLocation(message("lemma-location", view), view), null, "not a message of ours");
    assert.equal(readLocation(message(null, view), view), null);
});

test("the app in front asks for another app, at a path of its own", () => {
    const { view } = frame();
    assert.deepEqual(readOpen(message({ type: OPEN_MESSAGE_TYPE, id: "open-1", app: "deals", path: "/88" }, view), view), { id: "open-1", app: "deals", path: "/88" });
    assert.deepEqual(readOpen(message({ type: OPEN_MESSAGE_TYPE, app: "follow-ups" }, view), view), { id: "", app: "follow-ups", path: "/" }, "its one screen, when no path is named");
    for (const app of ["", "../people", "people/1042", "a b", 7, undefined]) {
        assert.equal(readOpen(message({ type: OPEN_MESSAGE_TYPE, app, path: "/" }, view), view), null, String(app));
    }
    assert.equal(readOpen(message({ type: OPEN_MESSAGE_TYPE, app: "deals", path: "88" }, view), view), null, "not a path");
    assert.equal(readOpen(message({ type: OPEN_MESSAGE_TYPE, app: "deals", path: "/88" }, frame().view), view), null, "another frame");
});

test("the frame that asked is told its app is being opened, so it does not go there itself", () => {
    const { view, told } = frame();
    const event = message({ type: OPEN_MESSAGE_TYPE, id: "open-1", app: "deals", path: "/88" }, view);
    const asked = readOpen(event, view);
    assert.ok(asked);
    acknowledgeOpen(event, asked);
    assert.deepEqual(told, [{ message: { type: OPEN_RESULT_MESSAGE_TYPE, id: "open-1" }, origin: "https://people.apps.example" }]);
});

test("the frame is told where the address says it is, at the origin it was loaded from", () => {
    const { view, told } = frame();
    tellLocation(view, "https://people.apps.example", "/1042");
    assert.deepEqual(told, [{ message: { type: LOCATION_MESSAGE_TYPE, path: "/1042" }, origin: "https://people.apps.example" }]);
});

test("a frame starts on the path the address named", () => {
    assert.equal(appUrlAt("https://people.apps.example", "/"), "https://people.apps.example");
    assert.equal(appUrlAt("https://people.apps.example", "/1042"), "https://people.apps.example/1042");
    assert.equal(appUrlAt("https://people.apps.example/", "/1042"), "https://people.apps.example/1042");
    assert.equal(appUrlAt("http://localhost:8000/apps/people/", "/1042"), "http://localhost:8000/apps/people/1042");
    assert.equal(appUrlAt("https://people.apps.example/?sample=1", "/1042"), "https://people.apps.example/1042?sample=1");
    assert.equal(appUrlAt("not a url", "/1042"), "not a url");
});
