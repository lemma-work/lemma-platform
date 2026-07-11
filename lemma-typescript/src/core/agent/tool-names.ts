const LEMMA_MCP_TOOL_PREFIXES = [
  "mcp.lemma_tools.",
  "mcp__lemma_tools__",
  "lemma_tools_",
  "lemma_tools.",
  "mcp.lemma-tools.",
  "mcp__lemma-tools__",
  "lemma-tools_",
  "lemma-tools.",
] as const;

/** Convert a provider-scoped Lemma MCP tool name to Lemma's canonical name.
 * Provider-native and third-party MCP tool names are intentionally unchanged. */
export function normalizeAgentToolName(toolName: string): string {
  let normalized = toolName.trim();
  const lower = normalized.toLowerCase();
  const providerPrefix = LEMMA_MCP_TOOL_PREFIXES.find((prefix) => lower.startsWith(prefix));
  if (providerPrefix) normalized = normalized.slice(providerPrefix.length);
  if (normalized.toLowerCase().startsWith("lemma_")) {
    normalized = normalized.slice("lemma_".length);
  }
  return normalized;
}
