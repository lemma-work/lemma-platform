import { describe, it, expect, afterEach, vi } from "vitest";
import {
  LEMMA_LOCATION_MESSAGE_TYPE,
  LEMMA_OPEN_MESSAGE_TYPE,
  LEMMA_OPEN_RESULT_MESSAGE_TYPE,
  appAddressAt,
  currentAppPath,
  isFramedByHost,
  moveAppTo,
  normalizeAppPath,
  openApp,
  watchAppPath,
} from "../browser-location.js";

/** Stand a host in front of the frame and keep what it is told. A host that
 *  answers says it is opening the app it was asked for. */
function host({ answers = false } = {}): any[] {
  const told: any[] = [];
  const parent = {
    postMessage: (message: any) => {
      told.push(message);
      if (answers && message?.type === LEMMA_OPEN_MESSAGE_TYPE) {
        window.dispatchEvent(new MessageEvent("message", {
          data: { type: LEMMA_OPEN_RESULT_MESSAGE_TYPE, id: message.id },
          source: window.parent as Window,
        }));
      }
    },
  };
  Object.defineProperty(window, "parent", { value: parent, configurable: true });
  return told;
}

function fromHost(data: unknown): void {
  window.dispatchEvent(new MessageEvent("message", { data, source: window.parent as Window }));
}

/** Catch where the page is sent, rather than sending it. */
function catchNavigation() {
  const assign = vi.fn();
  vi.spyOn(window, "location", "get").mockReturnValue({ ...window.location, href: window.location.href, assign } as Location);
  return assign;
}

/** Where the other app lives, as the API would say. */
const find = vi.fn(async (app: string) => `https://${app}-3fa9c1.apps.example.test`);

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
  find.mockClear();
  Object.defineProperty(window, "parent", { value: window, configurable: true });
  window.sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

describe("normalizeAppPath", () => {
  it("keeps a rooted path and drops a trailing slash", () => {
    expect(normalizeAppPath("/")).toBe("/");
    expect(normalizeAppPath("/1042")).toBe("/1042");
    expect(normalizeAppPath("/1042/")).toBe("/1042");
    expect(normalizeAppPath("/people/1042/notes")).toBe("/people/1042/notes");
  });

  it("refuses what is not a path inside an app", () => {
    for (const not of ["", "1042", "//evil.example", "/a//b", "/../x", "/a/./b", "/a?b=1", "/a#b", "/a b", "/a\\b", 7, null, undefined]) {
      expect(normalizeAppPath(not)).toBeNull();
    }
    expect(normalizeAppPath("/" + "a".repeat(600))).toBeNull();
  });
});

describe("appAddressAt", () => {
  it("puts the path after the app's address", () => {
    expect(appAddressAt("https://deals.apps.example.test", "/")).toBe("https://deals.apps.example.test/");
    expect(appAddressAt("https://deals.apps.example.test/", "/88")).toBe("https://deals.apps.example.test/88");
    expect(appAddressAt("http://localhost:8000/apps/deals/", "/88")).toBe("http://localhost:8000/apps/deals/88");
  });

  it("never makes an address out of something that is not a web address", () => {
    expect(appAddressAt("javascript:alert(1)", "/88")).toBeNull();
    expect(appAddressAt("deals.apps.example.test", "/88")).toBeNull();
    expect(appAddressAt("https://deals.apps.example.test", "88")).toBeNull();
  });
});

describe("an app on its own in a tab", () => {
  it("is not framed, and steps through its own history", () => {
    expect(isFramedByHost()).toBe(false);
    const before = window.history.length;
    expect(moveAppTo("/1042")).toBe("/1042");
    expect(currentAppPath()).toBe("/1042");
    expect(window.history.length).toBe(before + 1);
  });

  it("does not add a step for where it already is", () => {
    moveAppTo("/1042");
    const before = window.history.length;
    moveAppTo("/1042");
    expect(window.history.length).toBe(before);
  });

  it("hears Back as the path it lands on", () => {
    const heard: string[] = [];
    const stop = watchAppPath((path) => heard.push(path));
    window.history.replaceState(null, "", "/88");
    window.dispatchEvent(new PopStateEvent("popstate"));
    stop();
    expect(heard).toEqual(["/88"]);
  });

  it("goes to another app at its own address", async () => {
    const assign = catchNavigation();
    await expect(openApp("deals", "/88", find)).resolves.toBe(true);
    expect(find).toHaveBeenCalledWith("deals");
    expect(assign).toHaveBeenCalledWith("https://deals-3fa9c1.apps.example.test/88");
  });

  it("stays put when the other app's address cannot be found", async () => {
    const assign = catchNavigation();
    await expect(openApp("deals", "/88")).resolves.toBe(false);
    await expect(openApp("deals", "/88", async () => null)).resolves.toBe(false);
    await expect(openApp("deals", "/88", async () => { throw new Error("404"); })).resolves.toBe(false);
    await expect(openApp("deals", "/88", async () => "javascript:alert(1)")).resolves.toBe(false);
    expect(assign).not.toHaveBeenCalled();
  });

  it("opens nothing for a request that is not one", async () => {
    const assign = catchNavigation();
    await expect(openApp("", "/88", find)).resolves.toBe(false);
    await expect(openApp("deals", "88", find)).resolves.toBe(false);
    expect(find).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });
});

describe("an app framed by a host", () => {
  it("rewrites its own entry and tells the host where it moved", () => {
    const told = host();
    const before = window.history.length;
    expect(moveAppTo("/1042/")).toBe("/1042");
    expect(window.history.length).toBe(before);
    expect(currentAppPath()).toBe("/1042");
    expect(told).toEqual([{ type: LEMMA_LOCATION_MESSAGE_TYPE, path: "/1042" }]);
  });

  it("says nothing for a path that is not one", () => {
    const told = host();
    expect(moveAppTo("/a?b=1")).toBeNull();
    expect(told).toEqual([]);
  });

  it("asks the host for another app, and leaves it to the host once it answers", async () => {
    const told = host({ answers: true });
    const assign = catchNavigation();
    await expect(openApp(" deals ", "/88", find)).resolves.toBe(true);
    expect(told).toHaveLength(1);
    expect(told[0]).toMatchObject({ type: LEMMA_OPEN_MESSAGE_TYPE, app: "deals", path: "/88" });
    expect(typeof told[0].id).toBe("string");
    expect(find).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  it("goes itself when the host does not answer", async () => {
    vi.useFakeTimers();
    host();
    const assign = catchNavigation();
    const opening = openApp("deals", "/88", find);
    await vi.advanceTimersByTimeAsync(1500);
    await expect(opening).resolves.toBe(true);
    expect(assign).toHaveBeenCalledWith("https://deals-3fa9c1.apps.example.test/88");
  });

  it("does nothing inside an AI tool's conversation, whose frame holds this app's session", async () => {
    vi.useFakeTimers();
    window.history.replaceState(null, "", "/?lemma_embed=mcp");
    host();
    const assign = catchNavigation();
    const opening = openApp("deals", "/88", find);
    await vi.advanceTimersByTimeAsync(1500);
    await expect(opening).resolves.toBe(false);
    expect(find).not.toHaveBeenCalled();
    expect(assign).not.toHaveBeenCalled();
  });

  it("follows the host, and does not tell it back", () => {
    const told = host();
    const heard: string[] = [];
    const stop = watchAppPath((path) => heard.push(path));
    fromHost({ type: LEMMA_LOCATION_MESSAGE_TYPE, path: "/88" });
    stop();
    expect(heard).toEqual(["/88"]);
    expect(currentAppPath()).toBe("/88");
    expect(told).toEqual([]);
  });

  it("listens to the host only, and only for a path", () => {
    host();
    const heard: string[] = [];
    const stop = watchAppPath((path) => heard.push(path));
    window.dispatchEvent(new MessageEvent("message", {
      data: { type: LEMMA_LOCATION_MESSAGE_TYPE, path: "/from-a-sibling" },
      source: {} as Window,
    }));
    fromHost({ type: LEMMA_LOCATION_MESSAGE_TYPE, path: "//evil.example" });
    fromHost({ type: "something-else", path: "/88" });
    stop();
    fromHost({ type: LEMMA_LOCATION_MESSAGE_TYPE, path: "/after-stopping" });
    expect(heard).toEqual([]);
  });
});
