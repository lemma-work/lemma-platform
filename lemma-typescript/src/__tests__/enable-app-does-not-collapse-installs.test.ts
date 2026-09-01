import { describe, expect, it, vi } from "vitest";

import { ConnectorsNamespace } from "../namespaces/connectors.js";

/**
 * `enableApp` used to return ANY active install whose connector id matched,
 * ignoring the submitted kind, config and name.
 *
 * The backend deliberately permits many installs of one connector -- the
 * `auth_configs` model says there is deliberately no
 * `(organization_id, connector_id)` uniqueness -- so this was the SDK
 * enforcing, client-side and silently, a constraint the schema had dropped.
 * Every MCP server shares the catalog id `mcp`, every database shares `sql`,
 * every REST API shares `openapi`.
 */
function namespaceWith(existing: Array<Record<string, unknown>>) {
  const created: Array<Record<string, unknown>> = [];
  const listed = { items: existing, next_page_token: null };

  const connectors = new ConnectorsNamespace({
    // The namespace calls through `client.request(fn)`; the generated service
    // is what actually issues the call, so both are stubbed at that seam.
    request: (fn: () => unknown) => fn(),
  } as never);

  vi.spyOn(connectors.authConfigs, "list").mockResolvedValue(listed as never);
  vi.spyOn(connectors.authConfigs, "create").mockImplementation((async (
    _org: string,
    body: Record<string, unknown>,
  ) => {
    created.push(body);
    return { id: `created-${created.length}`, ...body };
  }) as never);

  return { connectors, created };
}

const anMcpInstall = {
  id: "existing-1",
  connector_id: "mcp",
  status: "ACTIVE",
  name: "docs-mcp",
};

describe("enableApp", () => {
  it("creates a second MCP server rather than handing back the first", async () => {
    const { connectors, created } = namespaceWith([anMcpInstall]);

    const result = await connectors.enableApp("org", "mcp", {
      name: "jira-mcp",
      kind: "mcp",
      config: { server_url: "https://jira.example.com/mcp" },
    } as never);

    expect(created).toHaveLength(1);
    expect(created[0]).toMatchObject({ name: "jira-mcp" });
    expect((result as { id: string }).id).not.toBe("existing-1");
  });

  it("does not drop an organization's own OAuth credentials", async () => {
    // The "use my own Slack app" flow. Returning the Lemma-managed install
    // dropped the submitted client id and secret, and OAuth then ran against
    // Lemma's app -- the one thing the flow exists to avoid.
    const { connectors, created } = namespaceWith([
      { id: "managed", connector_id: "slack", status: "ACTIVE" },
    ]);

    await connectors.enableApp("org", "slack", {
      config_source: "ORG_CUSTOM",
      config: { oauth2_credentials: { client_id: "ours", client_secret: "s" } },
    } as never);

    expect(created).toHaveLength(1);
    expect(created[0]).toMatchObject({ config_source: "ORG_CUSTOM" });
  });

  it("still reuses an install when asked only to turn a connector on", async () => {
    // The case the shortcut was written for: no name, no config, nothing that
    // distinguishes one install from another.
    const { connectors, created } = namespaceWith([
      { id: "existing-gmail", connector_id: "gmail", status: "ACTIVE" },
    ]);

    const result = await connectors.enableApp("org", "gmail");

    expect(created).toHaveLength(0);
    expect((result as { id: string }).id).toBe("existing-gmail");
  });

  it("does not hand back an install of a different kind", async () => {
    // Advanced setup, "Native OAuth", for an org already on Composio. The
    // caller then reads the kind off the returned row and runs the Composio
    // flow -- having been told it enabled the one they chose.
    const { connectors, created } = namespaceWith([
      { id: "composio-one", connector_id: "gmail", status: "ACTIVE", kind: "composio" },
    ]);

    await connectors.enableApp("org", "gmail", { kind: "http" } as never);

    expect(created).toHaveLength(1);
    expect(created[0]).toMatchObject({ kind: "http" });
  });

  it("reuses an install of the same kind when a kind is named", async () => {
    const { connectors, created } = namespaceWith([
      { id: "composio-one", connector_id: "gmail", status: "ACTIVE", kind: "composio" },
    ]);

    const result = await connectors.enableApp("org", "gmail", {
      kind: "composio",
    } as never);

    expect(created).toHaveLength(0);
    expect((result as { id: string }).id).toBe("composio-one");
  });

  it("prefers the default install, as every other resolver does", async () => {
    const { connectors } = namespaceWith([
      { id: "first-made", connector_id: "slack", status: "ACTIVE", kind: "http" },
      { id: "the-default", connector_id: "slack", status: "ACTIVE", kind: "http", is_default: true },
    ]);

    const result = await connectors.enableApp("org", "slack");

    expect((result as { id: string }).id).toBe("the-default");
  });

  it("ignores an install that is not active", async () => {
    const { connectors, created } = namespaceWith([
      { id: "disabled", connector_id: "gmail", status: "DISABLED" },
    ]);

    await connectors.enableApp("org", "gmail");

    expect(created).toHaveLength(1);
  });
});
