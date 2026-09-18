// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const captureEvent = vi.hoisted(() => vi.fn());
vi.mock("@/lib/analytics/client", () => ({ captureEvent }));

import { FIRST_RUN_TOUR_FRAMES, FirstRunTour, type FirstRunTourStatus } from "./first-run-tour";

let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
    captureEvent.mockClear();
    container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
});

async function render(status: FirstRunTourStatus, onOpen = vi.fn()) {
    await act(async () => root.render(<FirstRunTour status={status} onOpen={onOpen} />));
    return onOpen;
}

const button = (label: string) =>
    [...container.querySelectorAll("button")].find((item) => item.textContent?.trim() === label);

const claim = () => container.querySelector("h1")?.textContent;

async function press(label: string) {
    const target = button(label);
    expect(target, `no button labelled ${label}`).toBeDefined();
    await act(async () => target!.click());
}

describe("the first-run tour", () => {
    it("opens on the first frame with nowhere to go back to", async () => {
        await render("working");
        expect(claim()).toBe(FIRST_RUN_TOUR_FRAMES[0].claim);
        expect(button("Back")?.disabled).toBe(true);
        expect(container.textContent).toContain("1 of 4");
        expect(container.textContent).toContain("Setting up your workspace");
    });

    it("walks the frames in order and reports each one viewed once", async () => {
        await render("working");
        await press("Continue");
        expect(claim()).toBe(FIRST_RUN_TOUR_FRAMES[1].claim);
        await press("Continue");
        await press("Continue");
        expect(claim()).toBe(FIRST_RUN_TOUR_FRAMES[3].claim);
        expect(container.textContent).toContain("4 of 4");
        expect(captureEvent.mock.calls.map(([name, props]) => [name, props.step])).toEqual(
            FIRST_RUN_TOUR_FRAMES.map((frame) => ["onboarding.step_viewed", frame.id]),
        );
    });

    it("keeps saying Continue on the last frame until the workspace exists", async () => {
        const onOpen = await render("working");
        for (let i = 0; i < 3; i += 1) await press("Continue");
        expect(button("Continue")).toBeDefined();
        expect(button("Open my workspace")).toBeUndefined();
        expect(onOpen).not.toHaveBeenCalled();
    });

    it("offers to open the workspace on the last frame once it is ready", async () => {
        await render("ready");
        for (let i = 0; i < 3; i += 1) await press("Continue");
        expect(button("Open my workspace")).toBeDefined();
        expect(container.textContent).toContain("Your workspace is ready");
    });

    it("asks to go in exactly once when the person is done, and waits visibly if it must", async () => {
        const onOpen = await render("working");
        for (let i = 0; i < 4; i += 1) await press("Continue");
        expect(onOpen).toHaveBeenCalledTimes(1);
        // The tour is over; the only thing left on screen is the wait.
        expect(container.querySelector("h1")).toBeNull();
        expect(container.textContent).toContain("Setting up your workspace");
        // Becoming ready afterwards does not ask a second time.
        await render("ready", onOpen);
        expect(onOpen).toHaveBeenCalledTimes(1);
    });

    it("lets Skip end the tour from any frame", async () => {
        const onOpen = await render("working");
        await press("Continue");
        await press("Skip");
        expect(onOpen).toHaveBeenCalledTimes(1);
        expect(container.querySelector("h1")).toBeNull();
        // Skipping is not a frame view.
        expect(captureEvent).toHaveBeenCalledTimes(2);
    });

    it("moves with the arrow keys", async () => {
        await render("working");
        const key = (name: string) =>
            act(async () => {
                document.dispatchEvent(new KeyboardEvent("keydown", { key: name }));
            });
        await key("ArrowRight");
        expect(claim()).toBe(FIRST_RUN_TOUR_FRAMES[1].claim);
        await key("ArrowLeft");
        expect(claim()).toBe(FIRST_RUN_TOUR_FRAMES[0].claim);
        await key("ArrowLeft");
        expect(claim()).toBe(FIRST_RUN_TOUR_FRAMES[0].claim);
    });

    it("puts nothing focusable inside the pictures", async () => {
        await render("working");
        const scene = container.querySelector(".first-run-tour-scene");
        expect(scene).not.toBeNull();
        expect(scene!.querySelectorAll("button, a, input, [tabindex]").length).toBe(0);
    });
});
