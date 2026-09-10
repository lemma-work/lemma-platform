// @vitest-environment jsdom
import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ChatUsage } from "./chat-usage";

afterEach(cleanup);

function harness(children: ReactNode) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return createElement(QueryClientProvider, { client }, children);
}

describe("usage in the composer", () => {
  it.each([null, undefined, "CONFIGURATION_ERROR"])(
    "adds nothing to the composer while the allowance holds (%s)",
    (errorCode) => {
      const { container } = render(
        harness(
          <ChatUsage
            organizationId="org-a"
            running={false}
            conversationId="conversation-1"
            errorCode={errorCode}
          />,
        ),
      );
      expect(container.innerHTML).toBe("");
    },
  );
  it("points at the reset times once a limit has stopped a turn", () => {
    render(
      harness(
        <ChatUsage
          organizationId="org a"
          running={false}
          conversationId="conversation-1"
          errorCode="USAGE_LIMIT_EXCEEDED"
        />,
      ),
    );
    expect(
      screen.getByRole("link", { name: /Usage limit reached/ }).getAttribute("href"),
    ).toBe("/profile/usage?organizationId=org%20a");
  });
});
