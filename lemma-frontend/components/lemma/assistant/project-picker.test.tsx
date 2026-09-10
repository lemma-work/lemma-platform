// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectPicker } from "./project-picker";

// The popover measures itself, and jsdom has no ResizeObserver. A stub is
// enough: nothing here asserts on geometry.
class NoopResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}
(globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver ??= NoopResizeObserver;
// cmdk scrolls the highlighted item into view, which jsdom does not implement.
Element.prototype.scrollIntoView ??= function scrollIntoView() {};

afterEach(cleanup);

const base = {
  value: null,
  onChange: vi.fn(),
  projects: [],
  isLoadingProjects: false,
  connectHref: "/pod/p/connectors",
};

describe("choosing where a conversation works", () => {
  it("names the bound folder on the chip instead of Scratchpad", () => {
    render(
      <ProjectPicker
        {...base}
        isConnected={false}
        localFolder="/Users/me/projects/lemma"
        onPickLocalFolder={vi.fn()}
        canConnectGithub={false}
      />,
    );

    // The folder's name, not its whole path: the chip has room for one.
    expect(screen.getByText("lemma")).toBeTruthy();
    expect(screen.queryByText("Scratchpad")).toBeNull();
  });

  it("does not offer a GitHub connection that cannot be completed", async () => {
    // A GitHub App needs a registration, secrets and a reachable webhook. A
    // local install has none, so the button could never be finished.
    //
    // The menu has to be opened to assert this: the content lives in a popover,
    // so a closed picker would pass the same assertion while offering it.
    render(
      <ProjectPicker
        {...base}
        isConnected={false}
        onPickLocalFolder={vi.fn()}
        canConnectGithub={false}
      />,
    );
    await userEvent.click(screen.getByRole("button"));

    expect(screen.queryByText("Connect GitHub")).toBeNull();
    // And what it offers instead.
    expect(screen.getByText("Choose a folder…")).toBeTruthy();
  });

  it("still offers it where a connection is possible", async () => {
    render(<ProjectPicker {...base} isConnected={false} canConnectGithub />);
    await userEvent.click(screen.getByRole("button"));

    expect(screen.getByText("Connect GitHub")).toBeTruthy();
  });

  it("hands the folder choice to the shell rather than naming a path", async () => {
    const onPickLocalFolder = vi.fn();
    render(
      <ProjectPicker
        {...base}
        isConnected={false}
        onPickLocalFolder={onPickLocalFolder}
        canConnectGithub={false}
      />,
    );
    await userEvent.click(screen.getByRole("button"));
    await userEvent.click(screen.getByText("Choose a folder…"));

    // No argument: the dialog is the shell's, and so is the path it returns.
    expect(onPickLocalFolder).toHaveBeenCalledWith();
  });

  it("says nothing on a settled conversation that chose neither", () => {
    const { container } = render(
      <ProjectPicker {...base} isConnected readOnly />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows a settled conversation's folder", () => {
    render(
      <ProjectPicker {...base} isConnected readOnly localFolder="/Users/me/work/api" />,
    );
    expect(screen.getByText("api")).toBeTruthy();
  });
});
