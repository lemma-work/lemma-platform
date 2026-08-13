/** The name every run publishes for Lemma's own MCP server. */
const LEMMA_MCP_SERVER_NAME = "lemma_tools";

/**
 * Server names whose tools are Lemma's.
 *
 * One name is the contract: every run publishes `lemma_tools`, and every path —
 * pod agent, local agent over ACP — registers the server under it. The rest are
 * names Lemma itself shipped earlier (a hyphenated MCP config, and bare `lemma`
 * from Agent Host builds that named the server locally instead of using the
 * run's) and still sit in stored conversations. Longest first, so `lemma_tools`
 * reads as itself rather than as `lemma` followed by `_tools`.
 */
const LEMMA_MCP_SERVER_NAMES = [LEMMA_MCP_SERVER_NAME, "lemma-tools", "lemma"].sort(
  (left, right) => right.length - left.length,
);

/** How agents mark a name as coming from an MCP server at all. */
const MCP_MARKERS = ["mcp__", "mcp.", "mcp/"] as const;

/**
 * Whatever character joined the server name to the tool name. No `-`:
 * `lemma-tools` is a server name in its own right above, and treating `-` as a
 * separator would read someone else's `lemma-corp` server as ours.
 */
const NAMESPACE_SEPARATORS = /^[_./:]+/;

/**
 * Drop the MCP namespace an agent added, when the server named is Lemma's.
 *
 * The namespace means nothing on its own — it is there so one agent's
 * `exec_command` cannot collide with another's — so removing it is what turns a
 * provider's spelling back into the name Lemma uses everywhere else. A
 * third-party `mcp__github__create_issue` is left exactly as it is.
 */
function stripProviderNamespace(toolName: string): string {
  let candidate = toolName;
  const marker = MCP_MARKERS.find((value) => candidate.toLowerCase().startsWith(value));
  if (marker) candidate = candidate.slice(marker.length);

  for (const name of LEMMA_MCP_SERVER_NAMES) {
    if (!candidate.toLowerCase().startsWith(name)) continue;
    const remainder = candidate.slice(name.length);
    const withoutSeparator = remainder.replace(NAMESPACE_SEPARATORS, "");
    // A separator has to follow the server name, or `lemmatize` would read as
    // the `lemma` server's `tize`.
    if (withoutSeparator !== remainder) return withoutSeparator;
  }
  return toolName;
}

/** Convert a provider-scoped Lemma MCP tool name to Lemma's canonical name.
 * Provider-native and third-party MCP tool names are intentionally unchanged. */
export function normalizeAgentToolName(toolName: string): string {
  const normalized = stripProviderNamespace(toolName.trim());
  return normalized.toLowerCase().startsWith("lemma_")
    ? normalized.slice("lemma_".length)
    : normalized;
}
