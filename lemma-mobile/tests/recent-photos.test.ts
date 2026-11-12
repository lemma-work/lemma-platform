import { describe, expect, it, vi } from "vitest";

/* The stub's Platform is Android. */
vi.mock("expo-file-system/legacy", () => ({}));
const asked = vi.fn();
vi.mock("expo-media-library/legacy", () => ({ getPermissionsAsync: asked, requestPermissionsAsync: asked }));

import { recentPhotos } from "../src/ui/files-in-chat/recent-photos";

describe("recent photos on Android", () => {
    it("never reads the library, so the build needs no media permission", async () => {
        expect(await recentPhotos(true)).toEqual({ kind: "none" });
        expect(asked).not.toHaveBeenCalled();
    });
});
