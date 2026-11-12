import { describe, expect, it } from "vitest";
import { coverUrl } from "../src/ui/cover";

describe("an app's cover", () => {
    it("lives at /.lemma/cover.png on the app's own address", () => {
        expect(coverUrl("https://returns.apps.lemma.work/")).toBe("https://returns.apps.lemma.work/.lemma/cover.png");
        expect(coverUrl("https://returns.apps.lemma.work/orders?x=1#top")).toBe("https://returns.apps.lemma.work/.lemma/cover.png");
        expect(coverUrl("http://localhost:8761/apps/x")).toBe("http://localhost:8761/.lemma/cover.png");
    });

    it("has none without an address of its own", () => {
        expect(coverUrl(null)).toBeNull();
        expect(coverUrl("sample://returns")).toBeNull();
    });
});
