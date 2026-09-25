import { isThirdPartyMcpTool, normalizeAgentToolName } from "lemma-sdk";
import { humanizeAgentName } from "@/data/agent-names";

/** Which tool a call is, for everything in this app that keys on it.
 *
 *  One reading, from the SDK, because there used to be three here — the cards,
 *  the approvals and the resource cards each had their own — and they
 *  disagreed. The worst of the disagreements handed a card to the wrong tool:
 *  keeping "the part after the last `__`" turned somebody's
 *  `mcp__search__web_search` into Lemma's `web_search`, and drew Lemma's card
 *  over a payload it had never seen.
 *
 *  So a third-party MCP tool has no key at all, and nothing can claim it. The
 *  Agent Host says whose a tool is in `tool_source`, and that is believed over
 *  the name: its third-party tools arrive under their own bare names, which
 *  are indistinguishable from Lemma's. Old conversations still hold
 *  namespaced spellings (`mcp__lemma_tools__lemma_exec_command`), which the
 *  SDK reads back to the tool they always were.
 *
 *  Lower-cased with separators folded to `_`, because the names a card
 *  matches on are written that way and a harness's `Exec-Command` is the
 *  same tool. */
export function toolKey(name: unknown, metadata?: Record<string, unknown> | null): string {
    if (typeof name !== "string" || !name.trim()) return "";
    if (isThirdPartyMcpTool(name, metadata)) return "";
    return normalizeAgentToolName(name).toLowerCase().trim().replace(/[.:\-\s]/g, "_");
}

function textOf(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

/** A third-party name split into its server and its tool, when it is
 *  namespaced at all. `mcp__github__create_issue`, `mcp.github.create_issue`. */
function splitNamespace(name: string): { server: string; tool: string } | null {
    const match = /^mcp(?:__|[./])(.+?)(?:__|[./])(.+)$/i.exec(name);
    return match ? { server: match[1], tool: match[2] } : null;
}

/** A tool as a step reads it: "Exec command", or "Create issue · github".
 *
 *  Humanised from the normalized name rather than the raw one, which read
 *  "Mcp  lemma tools  lemma exec command" for every call a local agent made
 *  through Lemma's server. Somebody else's tool keeps its server beside it:
 *  the same name from two servers is two different tools. */
export function toolLabel(name: unknown, metadata?: Record<string, unknown> | null): string {
    if (typeof name !== "string" || !name.trim()) return "Tool";
    if (!isThirdPartyMcpTool(name, metadata)) return humanizeAgentName(normalizeAgentToolName(name));
    const split = splitNamespace(name);
    const server = textOf(metadata?.tool_server) || split?.server || "";
    const spoken = humanizeAgentName(split?.tool ?? name);
    return server ? spoken + " · " + server : spoken;
}

/** The adapter's own words for a call, when they are worth reading.
 *
 *  Only a local agent's native tools: for one of Lemma's MCP tools the harness
 *  titles the call with the namespaced name itself
 *  (`mcp.lemma_tools.lemma_exec_command`), which is exactly what this app is
 *  trying not to show. */
export function toolTitle(metadata?: Record<string, unknown> | null): string {
    if (textOf(metadata?.tool_source) !== "native") return "";
    return textOf(metadata?.tool_title);
}
