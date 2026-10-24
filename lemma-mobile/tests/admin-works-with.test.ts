import { describe, expect, it } from "vitest";
import {
    askableFrom, connectBody, connectProblem, disconnectSaid, fileItem, howItAsks, linksFrom, tableItem, worksWithLine,
} from "../src/areas/admin/works-with/model";
import { worksWithSample } from "../src/areas/admin/works-with/sample";
import { askerChoices, shareRows } from "@web/space/pod-links";

/** Other Lemmas a Lemma works with (PARITY.md, F): who it can ask, who may
 *  ask it, and what each reads, decided apart from drawing them. */

describe("reading the server", () => {
    it("reads the Lemmas it can ask, dropping a row with no id or name", () => {
        const read = askableFrom({ items: [
            { pod_id: "p2", name: "Renewals", icon_url: null, description: "Keeps renewals on time.", through_you: true, connected: true },
            { pod_id: "p3", name: "" },
            { name: "Nameless" },
        ] });
        expect(read).toEqual([{ id: "p2", name: "Renewals", iconUrl: null, description: "Keeps renewals on time.", throughYou: true, connected: true }]);
    });

    it("reads who may ask it and what each was shared", () => {
        const [link] = linksFrom([{ pod_id: "p4", name: "Launch", steward_name: "Sam Okafor", shared: [
            { resource_type: "datastore_table", resource_name: "tickets" },
            { resource_type: "folder", resource_name: "/faq" },
            { resource_type: "folder" },
        ] }]);
        expect(link).toMatchObject({ podId: "p4", name: "Launch", stewardName: "Sam Okafor", shared: [{ kind: "table", name: "tickets" }, { kind: "folder", name: "/faq" }] });
    });

    it("sends every share as read only", () => {
        expect(connectBody([{ kind: "table", name: "tickets" }, { kind: "folder", name: "/faq" }]).shares).toEqual([
            { resource_type: "datastore_table", resource_name: "tickets", permission_ids: ["datastore.table.read", "datastore.record.read"] },
            { resource_type: "folder", resource_name: "/faq", permission_ids: ["folder.read"] },
        ]);
    });

    it("tells a folder from a file and keeps who may read it", () => {
        expect(fileItem({ name: "faq", kind: "DIRECTORY", path: "/faq", visibility: "PUBLIC" })).toMatchObject({ kind: "folder", path: "/faq", visibility: "PUBLIC" });
        expect(fileItem({ name: "a.md", kind: "FILE", path: "/a.md" }).kind).toBe("file");
        expect(tableItem({ name: "tickets" })).toMatchObject({ kind: "table", path: "tickets" });
    });
});

describe("what it says", () => {
    it("draws the Settings line", () => {
        expect(worksWithLine(2, 1).text).toBe("Asks 2 · asked by 1");
        expect(worksWithLine(0, 1).text).toBe("asked by 1");
        expect(worksWithLine(0, 0).text).toBe("Nobody yet");
        expect(worksWithLine(null, 0).text).toBeNull();
    });

    it("says how it asks another", () => {
        expect(howItAsks({ name: "Books", throughYou: true, connected: true })).toBe("As you · Allowed by Books");
        expect(howItAsks({ name: "Books", throughYou: false, connected: true })).toBe("Allowed by Books");
    });

    it("says why connecting failed, never a status code", () => {
        const failed = (statusCode: number) => Object.assign(new Error("nope"), { statusCode });
        expect(connectProblem(failed(403), "Support")).toBe("Only an admin of Support can let another Lemma ask it.");
        expect(connectProblem(failed(409), "Support")).toBe("You can only connect a Lemma you’re in.");
        expect(connectProblem(failed(400), "Support")).toMatch(/Untick it/);
        expect(connectProblem(new Error("offline"), "Support")).toBe("That couldn’t be saved. Try again.");
    });

    it("says what stops when a connection ends", () => {
        expect(disconnectSaid({ name: "Launch", shared: [{ kind: "table", name: "tickets" }, { kind: "folder", name: "/faq" }] }, "Support"))
            .toBe("Launch will no longer be able to ask Support, and an answer it is waiting for won’t come back. It loses what was shared: tickets, /faq.");
        expect(disconnectSaid({ name: "Hiring", shared: [] }, "Renewals")).not.toMatch(/loses/);
    });
});

describe("the sample", () => {
    it("asks the Lemmas Ada is in as her, and the one that allowed it", async () => {
        const source = worksWithSample();
        const askable = await source.askable("sample-support-1140");
        expect(askable.map((one) => one.name).sort()).toEqual(["Books", "Launch", "Renewals"]);
        expect(askable.find((one) => one.name === "Books")).toMatchObject({ throughYou: true, connected: true });
        const links = await source.links("sample-support-1140");
        expect(worksWithLine(askable.length, links.length).text).toBe("Asks 3 · asked by 1");
    });

    it("offers only Lemmas Ada is in to connect, marking one already allowed", async () => {
        const source = worksWithSample();
        const choices = askerChoices(await source.askable("sample-support-1140"), await source.links("sample-support-1140"));
        expect(choices.find((one) => one.name === "Launch")?.connected).toBe(true);
        expect(choices.map((one) => one.name)).not.toContain("Hiring");
    });

    it("never offers somebody's own folder to share", async () => {
        const source = worksWithSample();
        const rows = shareRows((await source.shareable("sample-support-1140", "files", "/")).items);
        expect(rows.map((one) => one.share.name)).toEqual(["/faq", "/policies", "/reports"]);
        expect(rows.find((one) => one.share.name === "/reports")?.public).toBe(true);
    });

    it("connects, replacing what it read, and disconnects", async () => {
        const source = worksWithSample();
        await source.connect("sample-support-1140", "sample-launch-1440", [{ kind: "table", name: "customers" }]);
        expect((await source.links("sample-support-1140"))[0].shared).toEqual([{ kind: "table", name: "customers" }]);
        await expect(source.connect("sample-support-1140", "sample-hiring-1078", [])).rejects.toMatchObject({ statusCode: 409 });
        await source.disconnect("sample-support-1140", "sample-launch-1440");
        expect(await source.links("sample-support-1140")).toEqual([]);
    });
});
