"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  isConversationRunningStatus,
  latestAssistantText,
  normalizeConversationStatus,
} from "lemma-sdk";
import {
  Bot,
  Check,
  CheckCircle2,
  Circle,
  Code2,
  Copy,
  Database,
  FileAudio,
  FileCode2,
  FileSearch,
  FileStack,
  FileText,
  Folder,
  Globe2,
  Link2,
  ListTodo,
  MessageSquare,
  Mic,
  Search,
  Table2,
  Users,
  Volume2,
  Wrench,
} from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { formatAgentName } from "@/lib/utils/agents";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import { playSoundFeedback } from "@/lib/feedback/sound-feedback";
// Pure formatting / label / tool-payload helpers (extracted from assistant-experience).
import {
  asArray,
  asRecord,
  asString,
  compactCommand,
  countPatchLines,
  fileNameFromPath,
  firstArrayOfRecords,
  firstRecordString,
  firstToolArgString,
  humanizeKey,
  isCommandDetailTool,
  normalizeToolNameForDisplay,
  operationRecordsFromResult,
  parsePatchFileEntries,
  parseTodoItems,
  payloadValue,
  resultText,
  summarizeToolPayload,
  toolArg,
  toolStatusLabel,
  truncateLabel,
} from "./assistant-format";
import { currentPodIdFromBrowserPath } from "./assistant-resource-cards";
import type { ToolCardArgs, ToolCardResult } from "./assistant-experience";

type ToolStatus = { label: string; tone: "running" | "success" | "error" };

export function DetailsWithCopy({ label, value }: { label: string; value: string }) {
  const [isOpen, setIsOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const handleCopy = async (e: React.MouseEvent) => {
    e.preventDefault();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      playSoundFeedback('action-success');
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard access denied */ }
  };
  return (
    <details
      className="text-xs"
      open={isOpen}
      onToggle={(e) => setIsOpen((e.currentTarget as HTMLDetailsElement).open)}
    >
      <summary className="cursor-pointer list-none flex items-center justify-between text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)]">
        <span>{label}</span>
        {isOpen ? (
          <button
            onClick={handleCopy}
            className="inline-flex items-center gap-1 text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] transition-colors"
            title="Copy"
          >
            {copied
              ? <Check className="size-3 text-[var(--state-success)]" />
              : <Copy className="size-3" />}
          </button>
        ) : null}
      </summary>
      <div className="mt-1 overflow-x-auto rounded bg-[color:color-mix(in_srgb,var(--surface-2)_50%,transparent)] p-2">
        <pre className="lemma-assistant-text-primary-readable whitespace-pre-wrap break-words font-mono text-xs">{value}</pre>
      </div>
    </details>
  );
}

// ── Lean primitives ─────────────────────────────────────────────────────────
// One shared, low-chrome vocabulary so every tool card reads the same way: a
// single header line (icon · title · status), then a tight body. No boxed icon
// chips, no heavy meta grids.

function StatusPill({ status }: { status: ToolStatus }) {
  return (
    <span
      className={cn(
        "flex shrink-0 items-center gap-1.5 text-xs leading-4",
        status.tone === "success" && "text-[var(--state-success)]",
        status.tone === "error" && "text-[var(--state-error)]",
        status.tone === "running" && "text-[var(--action-primary)]",
      )}
    >
      <span className={cn("size-1.5 rounded-full bg-current", status.tone === "running" && "animate-pulse")} />
      {status.label}
    </span>
  );
}

function ToolBlock({
  icon,
  title,
  status,
  children,
}: {
  icon: ReactNode;
  title: string;
  status?: ToolStatus;
  children?: ReactNode;
}) {
  return (
    <div className="mt-1.5 overflow-hidden rounded-lg border border-[color:color-mix(in_srgb,var(--row-border)_72%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_96%,transparent)]">
      <div className="flex items-center justify-between gap-3 border-b border-[color:color-mix(in_srgb,var(--row-border)_45%,transparent)] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 text-[var(--text-tertiary)]">{icon}</span>
          <span className="truncate text-sm font-medium text-[var(--text-primary)]">{title}</span>
        </div>
        {status ? <StatusPill status={status} /> : null}
      </div>
      {children ? <div className="grid gap-2.5 p-3">{children}</div> : null}
    </div>
  );
}

function MetaRow({ entries }: { entries: Array<{ label: string; value?: string | number | null }> }) {
  const visible = entries.filter((entry) => entry.value !== null && typeof entry.value !== "undefined" && String(entry.value).trim().length > 0);
  if (visible.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
      {visible.map((entry) => (
        <span key={entry.label} className="inline-flex min-w-0 items-baseline gap-1.5">
          <span className="shrink-0 text-[var(--text-tertiary)]">{entry.label}</span>
          <span className="truncate text-[var(--text-secondary)]">{String(entry.value)}</span>
        </span>
      ))}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-xs text-[var(--text-tertiary)]">{text}</p>;
}

function Quote({ text }: { text: string }) {
  return (
    <div className="whitespace-pre-wrap break-words border-l-2 border-[color:color-mix(in_srgb,var(--row-border)_70%,transparent)] pl-2.5 text-sm leading-6 text-[var(--text-secondary)]">
      {text}
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  const visible = items.filter(Boolean);
  if (visible.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {visible.slice(0, 10).map((item, index) => (
        <span
          key={`${item}-${index}`}
          className="rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_54%,transparent)] px-2 py-1 text-xs text-[var(--text-secondary)]"
        >
          {item}
        </span>
      ))}
      {visible.length > 10 ? <span className="px-1 py-1 text-xs text-[var(--text-tertiary)]">+{visible.length - 10}</span> : null}
    </div>
  );
}

function CodeBlock({ label, value, tone = "default" }: { label?: string; value?: string | null; tone?: "default" | "error" }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      playSoundFeedback('action-success');
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard access denied */ }
  };

  if (!value || !value.trim()) return null;

  return (
    <div className="min-w-0">
      <div className="mb-1 flex items-center justify-between">
        {label ? <div className="text-xs font-medium text-[var(--text-tertiary)]">{label}</div> : <div />}
        <button
          onClick={handleCopy}
          className="inline-flex items-center gap-1 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] transition-colors"
          title="Copy"
        >
          {copied ? <Check className="size-3 text-[var(--state-success)]" /> : <Copy className="size-3" />}
        </button>
      </div>
      <pre
        className={cn(
          "max-h-80 overflow-auto rounded-md border p-2.5 font-mono text-xs leading-5",
          tone === "error"
            ? "border-current text-[var(--state-error)]"
            : "border-[color:color-mix(in_srgb,var(--row-border)_54%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-2)_26%,transparent)] text-[var(--text-primary)]",
        )}
      >
        <code className="whitespace-pre-wrap break-words">{value}</code>
      </pre>
    </div>
  );
}

function cellValue(value: unknown): string {
  if (value === null || typeof value === "undefined") return "—";
  if (typeof value === "string") return value.length ? value : "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function DataGrid({ rows }: { rows: Record<string, unknown>[] }) {
  const columns = useMemo(() => {
    const seen: string[] = [];
    rows.slice(0, 12).forEach((row) => {
      Object.keys(row).forEach((key) => {
        if (seen.length < 6 && !seen.includes(key)) seen.push(key);
      });
    });
    return seen;
  }, [rows]);
  const visible = rows.slice(0, 8);
  if (columns.length === 0) return null;

  return (
    <div className="max-h-72 overflow-auto rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)]">
      <table className="min-w-full table-fixed text-left text-xs">
        <thead className="sticky top-0 bg-[color:color-mix(in_srgb,var(--surface-2)_60%,transparent)]">
          <tr>
            {columns.map((column) => (
              <th
                key={column}
                className="border-b border-[color:color-mix(in_srgb,var(--row-border)_42%,transparent)] px-2.5 py-1.5 font-medium text-[var(--text-tertiary)]"
              >
                <span className="block truncate">{column}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visible.map((row, index) => (
            <tr key={index} className="border-b border-[color:color-mix(in_srgb,var(--row-border)_22%,transparent)] last:border-b-0">
              {columns.map((column) => (
                <td key={column} className="px-2.5 py-1.5 align-top text-[var(--text-secondary)]">
                  <span className="block max-w-48 truncate">{cellValue(row[column])}</span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ResultList({
  rows,
  titleKeys,
  detailKeys,
  icon,
}: {
  rows: Record<string, unknown>[];
  titleKeys: string[];
  detailKeys: string[];
  icon?: ReactNode;
}) {
  if (rows.length === 0) return null;
  return (
    <div className="grid gap-1.5">
      {rows.slice(0, 6).map((row, index) => {
        const title = firstRecordString(row, titleKeys) || `Result ${index + 1}`;
        const detail = firstRecordString(row, detailKeys);
        return (
          <div key={index} className="rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)] px-2.5 py-2">
            <div className="flex items-center gap-1.5">
              {icon ? <span className="shrink-0 text-[var(--text-tertiary)]">{icon}</span> : null}
              <span className="truncate text-xs font-medium text-[var(--text-primary)]">{title}</span>
            </div>
            {detail ? <div className="mt-1 line-clamp-2 text-xs leading-5 text-[var(--text-secondary)]">{detail}</div> : null}
          </div>
        );
      })}
      {rows.length > 6 ? <Empty text={`+${rows.length - 6} more`} /> : null}
    </div>
  );
}

// ── Terminal surface ─────────────────────────────────────────────────────────
// A genuine terminal window — traffic-light dots, monospace body, prompt glyphs,
// stderr in red — built entirely from semantic tokens so it tracks the theme.

type TermLine = { kind: "prompt" | "input" | "out" | "err" | "comment"; text: string; glyph?: string };

function TermRow({ line }: { line: TermLine }) {
  if (line.kind === "prompt") {
    return (
      <span className="block">
        <span className="select-none text-[var(--state-success)]">{`${line.glyph ?? "$"} `}</span>
        {line.text}
      </span>
    );
  }
  if (line.kind === "input") {
    return (
      <span className="block text-[var(--text-secondary)]">
        <span className="select-none text-[var(--action-primary)]">{`${line.glyph ?? "›"} `}</span>
        {line.text}
      </span>
    );
  }
  if (line.kind === "err") {
    return <span className="block text-[var(--state-error)]">{line.text}</span>;
  }
  if (line.kind === "comment") {
    return <span className="block text-[var(--text-tertiary)]">{line.text}</span>;
  }
  return <span className="block text-[var(--text-secondary)]">{line.text}</span>;
}

function TerminalSurface({
  title,
  status,
  lines,
  exitCode,
}: {
  title: string;
  status: ToolStatus;
  lines: TermLine[];
  exitCode?: number | string | null;
}) {
  const hasExit = exitCode !== null && typeof exitCode !== "undefined" && String(exitCode).length > 0;
  return (
    <div className="mt-1.5 overflow-hidden rounded-lg border border-[var(--row-border)] bg-[color:color-mix(in_srgb,var(--surface-2)_28%,transparent)] shadow-[var(--shadow-sm)]">
      <div className="flex items-center gap-2 border-b border-[var(--row-border)] bg-[color:color-mix(in_srgb,var(--surface-2)_55%,transparent)] px-3 py-2">
        <span className="flex items-center gap-1.5" aria-hidden="true">
          <span className="size-2.5 rounded-full bg-[var(--state-error)] opacity-80" />
          <span className="size-2.5 rounded-full bg-[var(--state-warning)] opacity-80" />
          <span className="size-2.5 rounded-full bg-[var(--state-success)] opacity-80" />
        </span>
        <span className="ml-1 min-w-0 flex-1 truncate font-mono text-xs text-[var(--text-tertiary)]">{title}</span>
        <StatusPill status={status} />
      </div>
      <pre className="max-h-[30rem] overflow-auto px-3 py-2.5 font-mono text-xs leading-6 text-[var(--text-primary)]">
        <code className="block whitespace-pre-wrap break-words">
          {lines.map((line, index) => (
            <TermRow key={index} line={line} />
          ))}
          {hasExit ? (
            <span className={cn("mt-1 block", String(exitCode) === "0" ? "text-[var(--text-tertiary)]" : "text-[var(--state-error)]")}>
              {`exit ${exitCode}`}
            </span>
          ) : null}
        </code>
      </pre>
    </div>
  );
}

function CommandToolDetails({
  normalizedName,
  args,
  state,
  result,
}: {
  normalizedName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const status = toolStatusLabel(state, result);
  const action = (firstToolArgString(args, ["action"]) || "").toLowerCase();
  const cmd = firstToolArgString(args, ["cmd", "command"]) || firstRecordString(result, ["cmd", "command"]);
  const processId = firstToolArgString(args, ["process_id", "processId", "session_id", "sessionId"])
    || firstRecordString(result, ["process_id", "processId", "session_id", "sessionId"]);
  const chars = asString(toolArg(args, "chars"));
  const workdir = firstToolArgString(args, ["workdir", "cwd"]) || firstRecordString(result, ["workdir", "cwd"]);
  const exitCode = payloadValue(result, ["exit_code", "exitCode"]);
  const stdout = resultText(result, ["stdout", "output"]);
  const stderr = resultText(result, ["stderr", "error"]);
  const processes = asArray(result.processes).map(asRecord).filter((record) => Object.keys(record).length > 0);

  // `manage_process` carries an `action`; translate it to the legacy verbs the
  // transcript already knows how to narrate.
  const effective = normalizedName === "manage_process"
    ? (action === "kill" ? "terminate_process" : action === "list" ? "list_processes" : "write_stdin")
    : normalizedName;

  const lines: TermLine[] = [];
  if (cmd) {
    lines.push({ kind: "prompt", text: compactCommand(cmd) });
  } else if (effective === "write_stdin") {
    lines.push(chars ? { kind: "input", text: chars.replace(/\n$/, "") } : { kind: "comment", text: "# poll output" });
  } else if (effective === "terminate_process") {
    lines.push({ kind: "comment", text: processId ? `# stopped ${processId}` : "# stopped process" });
  } else if (effective === "list_processes") {
    lines.push({ kind: "prompt", text: "jobs" });
  }
  processes.slice(0, 8).forEach((process) => {
    const processCmd = asString(process.cmd) || "workspace process";
    const pid = asString(process.process_id) || asString(process.processId);
    const completed = typeof process.completed === "boolean" ? (process.completed ? "done" : "running") : null;
    lines.push({ kind: "out", text: [pid ? `[${pid}]` : null, completed, processCmd].filter(Boolean).join(" ") });
  });
  if (stdout) lines.push({ kind: "out", text: stdout });
  if (stderr) lines.push({ kind: "err", text: stderr });
  if (lines.length === 0) lines.push({ kind: "comment", text: "# no output" });

  const title = cmd
    ? (workdir || "bash")
    : effective === "list_processes"
      ? "processes"
      : processId
        ? `process ${processId}`
        : (workdir || "bash");

  return (
    <TerminalSurface
      title={title}
      status={status}
      lines={lines}
      exitCode={typeof exitCode === "number" || typeof exitCode === "string" ? exitCode : null}
    />
  );
}

function PythonToolDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const code = asString(toolArg(args, "code")) || "";
  const out = resultText(result, ["result", "value", "output", "stdout"]);
  const err = resultText(result, ["stderr", "error", "traceback"]);

  const lines: TermLine[] = [];
  code.split("\n").forEach((codeLine, index) => {
    lines.push({ kind: "prompt", text: codeLine, glyph: index === 0 ? ">>>" : "..." });
  });
  if (lines.length === 0) lines.push({ kind: "comment", text: "# no code" });
  if (out) lines.push({ kind: "out", text: out });
  if (err) lines.push({ kind: "err", text: err });

  return <TerminalSurface title="python3" status={toolStatusLabel(state, result)} lines={lines} />;
}

function PatchToolDetails({
  normalizedName,
  args,
  state,
  result,
}: {
  normalizedName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const patch = asString(toolArg(args, "patch")) || asString(toolArg(args, "edit_diff"));
  const path = firstToolArgString(args, ["file_path", "path", "target_file"]);
  const files = patch ? parsePatchFileEntries(patch) : [];
  const counts = patch ? countPatchLines(patch) : { added: 0, removed: 0 };
  const title = normalizedName === "edit_file" ? "Edited file" : normalizedName === "create_file" ? "Created file" : "Applied patch";

  return (
    <ToolBlock icon={<FileCode2 className="size-3.5" />} title={title} status={toolStatusLabel(state, result)}>
      <MetaRow
        entries={[
          { label: "File", value: path ? fileNameFromPath(path) : files[0]?.path },
          { label: "Files", value: files.length > 1 ? files.length : undefined },
          { label: "+", value: counts.added || undefined },
          { label: "−", value: counts.removed || undefined },
        ]}
      />
      {files.length > 1 ? <Chips items={files.map((entry) => `${entry.action} ${fileNameFromPath(entry.path)}`)} /> : null}
      <CodeBlock label={normalizedName === "edit_file" ? "Diff" : "Patch"} value={patch} />
      {!patch ? <CodeBlock label="Content" value={asString(toolArg(args, "content"))} /> : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function FileToolDetails({
  normalizedName,
  args,
  state,
  result,
}: {
  normalizedName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const path = firstToolArgString(args, ["file_path", "path"]) || firstRecordString(result, ["file_path", "path"]);
  const filePaths = asArray(toolArg(args, "file_paths")).map((value) => String(value)).filter(Boolean);
  const title = normalizedName === "file_processor"
    ? "Analyzed files"
    : normalizedName === "view_image"
      ? "Viewed image"
      : normalizedName === "create_file"
        ? "Created file"
        : "Read file";

  return (
    <ToolBlock icon={<FileSearch className="size-3.5" />} title={title} status={toolStatusLabel(state, result)}>
      <MetaRow
        entries={[
          { label: "Path", value: path ? fileNameFromPath(path) : undefined },
          { label: "Files", value: filePaths.length || undefined },
          { label: "Type", value: firstRecordString(result, ["media_type"]) },
        ]}
      />
      {filePaths.length > 0 ? <Chips items={filePaths.map(fileNameFromPath)} /> : null}
      <CodeBlock label="Goal" value={asString(toolArg(args, "goal"))} />
      <CodeBlock label="Content" value={asString(toolArg(args, "content")) || resultText(result, ["content", "result", "message", "text"])} />
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function SearchToolDetails({
  args,
  state,
  result,
}: {
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const query = firstToolArgString(args, ["query", "q", "search"]);
  const connectorId = firstToolArgString(args, ["connector_id", "app", "connector", "application_id"]);
  const rows = operationRecordsFromResult(result);
  const title = connectorId ? "Operations" : "Web search";

  return (
    <ToolBlock icon={connectorId ? <Wrench className="size-3.5" /> : <Globe2 className="size-3.5" />} title={title} status={toolStatusLabel(state, result)}>
      <MetaRow
        entries={[
          { label: "Query", value: query },
          { label: "App", value: connectorId },
          { label: "Results", value: rows.length || undefined },
        ]}
      />
      {rows.length > 0 ? (
        <ResultList
          rows={rows}
          titleKeys={["title", "name", "operation_name", "operationName", "id"]}
          detailKeys={["snippet", "description", "summary", "url", "link"]}
        />
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function SkillToolDetails({
  normalizedName,
  args,
  state,
  result,
}: {
  normalizedName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const name = firstToolArgString(args, ["name"]);
  const resources = asArray(result.resources).map(asRecord).filter((record) => Object.keys(record).length > 0);
  const skills = asArray(result.skills).map(asRecord).filter((record) => Object.keys(record).length > 0);
  const title = normalizedName.includes("resource") ? "Loaded skill resource" : normalizedName.includes("load") ? "Loaded skill" : "Listed skills";
  const content = resultText(result, ["content"]);

  return (
    <ToolBlock icon={<Code2 className="size-3.5" />} title={title} status={toolStatusLabel(state, result)}>
      <MetaRow
        entries={[
          { label: "Skill", value: name || firstRecordString(result, ["name"]) },
          { label: "Resource", value: firstToolArgString(args, ["resource_path"]) || firstRecordString(result, ["resource_path"]) },
          { label: "Items", value: skills.length || resources.length || undefined },
        ]}
      />
      <Chips
        items={[...skills, ...resources]
          .map((entry) => firstRecordString(entry, ["name", "path", "resource_path"]) || "")
          .filter(Boolean)}
      />
      <CodeBlock label="Content" value={content ? truncateLabel(content, 2400) : undefined} />
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function CallableToolDetails({
  normalizedName,
  args,
  state,
  result,
}: {
  normalizedName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const isAgent = normalizedName.startsWith("agent_");
  const prefix = isAgent ? "agent_" : "function_";
  const entityName = humanizeKey(normalizedName.slice(prefix.length) || prefix.replace("_", ""));
  const inputEntries = summarizeToolPayload(args).slice(0, 4);
  const outputEntries = summarizeToolPayload(result, { excludeKeys: ["success"] }).slice(0, 4);

  return (
    <ToolBlock
      icon={isAgent ? <Bot className="size-3.5" /> : <Wrench className="size-3.5" />}
      title={`${isAgent ? "Called" : "Ran"} ${entityName}`}
      status={toolStatusLabel(state, result)}
    >
      <MetaRow entries={[...inputEntries, ...outputEntries].map((entry) => ({ label: humanizeKey(entry.key), value: entry.value }))} />
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

// ── Plan / to-dos ────────────────────────────────────────────────────────────

function TodosDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const todos = parseTodoItems(args, result);
  const done = todos.filter((todo) => todo.state === "done").length;

  return (
    <ToolBlock
      icon={<ListTodo className="size-3.5" />}
      title={todos.length ? `Plan · ${done}/${todos.length}` : "Plan"}
      status={toolStatusLabel(state, result)}
    >
      {todos.length ? (
        <ul className="grid gap-1.5">
          {todos.map((todo, index) => (
            <li key={index} className="flex items-start gap-2 text-sm">
              <span className="mt-0.5 shrink-0">
                {todo.state === "done" ? (
                  <CheckCircle2 className="size-4 text-[var(--state-success)]" />
                ) : todo.state === "active" ? (
                  <Circle className="size-4 animate-pulse text-[var(--action-primary)]" />
                ) : (
                  <Circle className="size-4 text-[var(--text-tertiary)]" />
                )}
              </span>
              <span
                className={cn(
                  "min-w-0 leading-6",
                  todo.state === "done"
                    ? "text-[var(--text-tertiary)] line-through"
                    : todo.state === "active"
                      ? "text-[var(--text-primary)]"
                      : "text-[var(--text-secondary)]",
                )}
              >
                {todo.text}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <Empty text="No steps." />
      )}
    </ToolBlock>
  );
}

// ── Pod data tools ───────────────────────────────────────────────────────────

function PodDataTableDetails({
  normalizedName,
  args,
  state,
  result,
}: {
  normalizedName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}) {
  const isQuery = normalizedName === "pod_query";
  let rows = firstArrayOfRecords(result, ["items", "records", "rows", "results"]);
  if (!rows.length) {
    const single = asRecord(payloadValue(result, ["record"]));
    if (Object.keys(single).length > 0) rows = [single];
  }
  const total = payloadValue(result, ["total", "count"]);
  const sql = firstToolArgString(args, ["sql"]);
  const table = firstToolArgString(args, ["table_name", "table"]);
  const rowCount = typeof total === "number" ? total : rows.length;
  const title = isQuery ? "Query result" : (table || "Records");

  return (
    <ToolBlock icon={<Database className="size-3.5" />} title={title} status={toolStatusLabel(state, result)}>
      <MetaRow
        entries={[
          { label: "Table", value: !isQuery ? table : undefined },
          { label: "Rows", value: rowCount || (state === "result" ? "0" : undefined) },
        ]}
      />
      {sql ? <CodeBlock label="SQL" value={sql} /> : null}
      {rows.length ? (
        <DataGrid rows={rows} />
      ) : state === "result" && !resultText(result, ["error"]) ? (
        <Empty text="No rows returned." />
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function PodTablesDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const single = firstToolArgString(args, ["table_name"]);
  const tables = firstArrayOfRecords(result, ["tables", "items"]);
  const columns = firstArrayOfRecords(result, ["columns"]);

  return (
    <ToolBlock icon={<Table2 className="size-3.5" />} title={single ? `Table · ${single}` : "Tables"} status={toolStatusLabel(state, result)}>
      {tables.length ? (
        <div className="grid gap-1.5">
          {tables.slice(0, 12).map((table, index) => {
            const name = firstRecordString(table, ["name", "table_name", "table"]) || `Table ${index + 1}`;
            const cols = asArray(payloadValue(table, ["columns"])).length;
            const rls = payloadValue(table, ["rls", "rls_enabled"]) === true;
            const meta = [cols ? `${cols} cols` : null, rls ? "RLS" : null].filter(Boolean).join(" · ");
            return (
              <div key={index} className="flex items-center justify-between gap-3 rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)] px-2.5 py-1.5">
                <span className="truncate text-xs font-medium text-[var(--text-primary)]">{name}</span>
                {meta ? <span className="shrink-0 text-xs text-[var(--text-tertiary)]">{meta}</span> : null}
              </div>
            );
          })}
          {tables.length > 12 ? <Empty text={`+${tables.length - 12} more`} /> : null}
        </div>
      ) : columns.length ? (
        <Chips items={columns.map((column) => firstRecordString(column, ["name", "column"]) || "")} />
      ) : (
        <Empty text="No tables." />
      )}
    </ToolBlock>
  );
}

function PodFilesDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const path = firstToolArgString(args, ["path"]);
  const entries = firstArrayOfRecords(result, ["files", "entries", "items", "children", "results"]);

  return (
    <ToolBlock icon={<Folder className="size-3.5" />} title={path ? `Files · ${path}` : "Files"} status={toolStatusLabel(state, result)}>
      {entries.length ? (
        <div className="grid gap-1">
          {entries.slice(0, 14).map((entry, index) => {
            const name = firstRecordString(entry, ["name", "path", "file_path"]) || `Item ${index + 1}`;
            const kind = (firstRecordString(entry, ["type", "kind"]) || "").toLowerCase();
            const isDir = entry.is_dir === true || entry.is_directory === true || kind === "folder" || kind === "directory";
            return (
              <div key={index} className="flex items-center gap-2 text-xs">
                <span className="shrink-0 text-[var(--text-tertiary)]">
                  {isDir ? <Folder className="size-3.5" /> : <FileText className="size-3.5" />}
                </span>
                <span className="truncate text-[var(--text-secondary)]">{fileNameFromPath(name)}</span>
              </div>
            );
          })}
          {entries.length > 14 ? <Empty text={`+${entries.length - 14} more`} /> : null}
        </div>
      ) : (
        <Empty text="Empty folder." />
      )}
    </ToolBlock>
  );
}

function PodReadFileDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const path = firstToolArgString(args, ["path"]);
  const format = firstToolArgString(args, ["format"]);
  const content = resultText(result, ["content", "text", "markdown"]);

  return (
    <ToolBlock icon={<FileText className="size-3.5" />} title={path ? fileNameFromPath(path) : "File"} status={toolStatusLabel(state, result)}>
      <MetaRow entries={[{ label: "Path", value: path }, { label: "Format", value: format }]} />
      <CodeBlock value={content ? truncateLabel(content, 4000) : undefined} />
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function PodSearchFilesDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const query = firstToolArgString(args, ["query"]);
  const rows = firstArrayOfRecords(result, ["results", "items", "matches", "files"]);

  return (
    <ToolBlock icon={<FileSearch className="size-3.5" />} title="File search" status={toolStatusLabel(state, result)}>
      <MetaRow entries={[{ label: "Query", value: query }, { label: "Results", value: rows.length || undefined }]} />
      {rows.length ? (
        <ResultList
          rows={rows}
          titleKeys={["path", "name", "title", "file_path"]}
          detailKeys={["snippet", "text", "summary", "content"]}
          icon={<FileText className="size-3" />}
        />
      ) : state === "result" ? (
        <Empty text="No matches." />
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function PodFileUrlDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const path = firstToolArgString(args, ["path"]);
  const href = firstRecordString(result, ["app_url", "appUrl"]) || firstRecordString(result, ["url", "signed_url", "signedUrl", "download_url"]);

  return (
    <ToolBlock icon={<Link2 className="size-3.5" />} title="File link" status={toolStatusLabel(state, result)}>
      <div className="flex items-center justify-between gap-3 rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)] px-2.5 py-2">
        <span className="flex min-w-0 items-center gap-2">
          <FileText className="size-3.5 shrink-0 text-[var(--text-tertiary)]" />
          <span className="truncate text-xs text-[var(--text-secondary)]">{path ? fileNameFromPath(path) : "file"}</span>
        </span>
        {href ? (
          <a
            href={href}
            target="_blank"
            rel="noreferrer"
            className="shrink-0 text-xs font-medium text-[var(--action-primary)] hover:underline"
          >
            Open
          </a>
        ) : null}
      </div>
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function PodDocumentPagesDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const path = firstToolArgString(args, ["path"]);
  const start = payloadValue(args, ["page_start"]);
  const end = payloadValue(args, ["page_end"]);
  const pages = typeof start === "number"
    ? (typeof end === "number" && end !== start ? `${start}–${end}` : `${start}`)
    : undefined;

  return (
    <ToolBlock icon={<FileStack className="size-3.5" />} title={path ? fileNameFromPath(path) : "Document"} status={toolStatusLabel(state, result)}>
      <MetaRow entries={[{ label: "Pages", value: pages }, { label: "Path", value: path }]} />
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function PodWriteRecordDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const action = (firstToolArgString(args, ["action"]) || "write").toLowerCase();
  const table = firstToolArgString(args, ["table_name", "table"]);
  const recordId = firstToolArgString(args, ["record_id"]) || firstRecordString(result, ["id", "record_id"]);
  const data = asRecord(toolArg(args, "data"));
  const tone = action === "delete"
    ? "text-[var(--state-error)]"
    : action === "create"
      ? "text-[var(--state-success)]"
      : "text-[var(--state-warning)]";
  const dataEntries = summarizeToolPayload(data).map((entry) => ({ label: humanizeKey(entry.key), value: entry.value }));

  return (
    <ToolBlock icon={<Database className="size-3.5" />} title={`${humanizeKey(action)} record`} status={toolStatusLabel(state, result)}>
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("rounded-full border border-current px-2 py-0.5 text-xs leading-4", tone)}>{action}</span>
        {table ? <span className="text-xs text-[var(--text-secondary)]">{table}</span> : null}
        {recordId ? <span className="text-xs text-[var(--text-tertiary)]">#{recordId}</span> : null}
      </div>
      {dataEntries.length ? <MetaRow entries={dataEntries} /> : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

// ── Sub-agents ───────────────────────────────────────────────────────────────

function childStatusLabel(status: string | undefined): ToolStatus {
  const normalized = normalizeConversationStatus(status);
  if (!normalized) return { label: "Running", tone: "running" };
  if (["FAILED", "ERROR", "CANCELLED", "CANCELED"].includes(normalized)) {
    return { label: humanizeKey(normalized.toLowerCase()), tone: "error" };
  }
  if (normalized === "WAITING") return { label: "Waiting", tone: "running" };
  if (isConversationRunningStatus(normalized) || normalized === "QUEUED" || normalized === "PENDING") {
    return { label: "Running", tone: "running" };
  }
  return { label: "Done", tone: "success" };
}

/** Live window into a child conversation: polls status + messages while the
 * sub-agent runs and stops once it settles. Returns nulls when there is no
 * conversation to watch (the hook always runs, but the query stays disabled).
 * Only reached when the user expands the tool detail, so collapsed cards never
 * poll. */
function useSubagentLive(conversationId?: string) {
  const podId = currentPodIdFromBrowserPath();
  const enabled = Boolean(podId && conversationId);
  const { data, isLoading } = useQuery({
    queryKey: ["subagent-live", podId, conversationId ?? null],
    enabled,
    refetchInterval: (query) => {
      const status = (query.state.data as { conv?: { status?: string } } | undefined)?.conv?.status;
      return isConversationRunningStatus(status) || normalizeConversationStatus(status) === "WAITING" ? 2000 : false;
    },
    queryFn: async () => {
      const conversations = getLemmaClient(podId as string).conversations;
      const [conv, messages] = await Promise.all([
        conversations.get(conversationId as string),
        conversations.messages.list(conversationId as string, { limit: 40 }),
      ]);
      return { conv, items: asArray((messages as { items?: unknown }).items) };
    },
  });

  const status = asString(asRecord(data?.conv).status);
  const running = isConversationRunningStatus(status) || normalizeConversationStatus(status) === "WAITING";
  const latest = latestAssistantText((data?.items ?? []) as Parameters<typeof latestAssistantText>[0]);
  return {
    enabled,
    status,
    running,
    latest,
    loading: enabled && isLoading,
    settled: enabled && !running && !isLoading,
  };
}

function OutputText({ text }: { text: string }) {
  return <div className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-secondary)]">{text}</div>;
}

function subagentTask(input: unknown): string | undefined {
  if (typeof input === "string") return input;
  const record = asRecord(input);
  const text = asString(payloadValue(record, ["task", "prompt", "message", "input", "instruction"]));
  if (text) return text;
  return Object.keys(record).length ? JSON.stringify(input) : undefined;
}

function SpawnSubagentDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const agentName = firstToolArgString(args, ["agent_name", "agentName"]);
  const task = subagentTask(toolArg(args, "input"));
  const conversationId = firstRecordString(result, ["conversation_id", "conversationId"]);
  const childStatus = firstRecordString(result, ["status"]);
  const staticOutput = resultText(result, ["output", "message", "text", "result"]);
  const live = useSubagentLive(conversationId);

  const title = agentName ? `Sub-agent · ${formatAgentName(agentName)}` : "Sub-agent";
  const headerStatus = conversationId ? childStatusLabel(live.status ?? childStatus) : toolStatusLabel(state, result);
  const output = conversationId ? (live.latest || (live.settled ? staticOutput : undefined)) : staticOutput;

  return (
    <ToolBlock icon={<Bot className="size-3.5" />} title={title} status={headerStatus}>
      {task ? <p className="line-clamp-2 text-xs leading-5 text-[var(--text-tertiary)]">{task}</p> : null}
      {output ? (
        <OutputText text={truncateLabel(output, 1200)} />
      ) : live.running || live.loading ? (
        <span className="animate-pulse text-xs text-[var(--text-tertiary)]">Working…</span>
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function SubagentInteractionDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const action = (firstToolArgString(args, ["action"]) || "send").toLowerCase();
  const content = asString(toolArg(args, "content"));
  const conversationId = firstToolArgString(args, ["conversation_id", "conversationId"]) || firstRecordString(result, ["conversation_id", "conversationId"]);
  const staticOutput = resultText(result, ["output", "message", "text", "result"]);
  const live = useSubagentLive(conversationId);
  const title = action === "stop" ? "Stopped sub-agent" : action === "await" ? "Awaited sub-agent" : "Messaged sub-agent";
  const headerStatus = conversationId ? childStatusLabel(live.status) : toolStatusLabel(state, result);
  const output = live.latest || (live.settled || !conversationId ? staticOutput : undefined);

  return (
    <ToolBlock icon={<MessageSquare className="size-3.5" />} title={title} status={headerStatus}>
      {content ? <p className="line-clamp-2 text-xs leading-5 text-[var(--text-tertiary)]">{content}</p> : null}
      {output ? (
        <OutputText text={truncateLabel(output, 1200)} />
      ) : live.running || live.loading ? (
        <span className="animate-pulse text-xs text-[var(--text-tertiary)]">Working…</span>
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function QuerySubagentsDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const mode = (firstToolArgString(args, ["mode"]) || "list").toLowerCase();
  const rows = firstArrayOfRecords(result, mode === "messages" ? ["messages", "items"] : ["conversations", "children", "items", "subagents"]);

  return (
    <ToolBlock icon={<Users className="size-3.5" />} title={mode === "messages" ? "Sub-agent messages" : "Sub-agents"} status={toolStatusLabel(state, result)}>
      {rows.length ? (
        mode === "messages" ? (
          <ResultList rows={rows} titleKeys={["role", "author", "name"]} detailKeys={["content", "text", "message"]} />
        ) : (
          <ResultList
            rows={rows}
            titleKeys={["agent_name", "name", "title", "conversation_id"]}
            detailKeys={["status", "latest_status", "state"]}
            icon={<Bot className="size-3" />}
          />
        )
      ) : (
        <Empty text="No sub-agents." />
      )}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

// ── Speech ───────────────────────────────────────────────────────────────────

function SayDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const text = firstToolArgString(args, ["text"]);
  const path = firstRecordString(result, ["audio_file_path", "audioFilePath"]) || firstToolArgString(args, ["output_file_path"]);
  const podId = currentPodIdFromBrowserPath();
  const { data } = useQuery({
    queryKey: ["assistant-say-audio", podId, path],
    enabled: Boolean(podId && path && state === "result"),
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      if (!podId || !path) return null;
      return getLemmaClient(podId).files.getUrl(path);
    },
  });
  const src = asString(payloadValue(asRecord(data), ["url"]));

  return (
    <ToolBlock icon={<Volume2 className="size-3.5" />} title="Speech" status={toolStatusLabel(state, result)}>
      {text ? <Quote text={truncateLabel(text, 400)} /> : null}
      {src ? (
        <audio controls preload="none" src={src} className="h-9 w-full" />
      ) : path ? (
        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
          <FileAudio className="size-3.5" />
          {fileNameFromPath(path)}
        </div>
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

// ── Tool discovery ───────────────────────────────────────────────────────────

function ToolSearchDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const queries = asArray(toolArg(args, "queries")).map((value) => String(value)).filter(Boolean);
  const single = firstToolArgString(args, ["query"]);
  const allQueries = queries.length ? queries : single ? [single] : [];
  const toolRows = firstArrayOfRecords(result, ["discovered_tools", "tools", "results", "items", "operations", "functions"]);
  const hasDetail = toolRows.some((row) => firstRecordString(row, ["description", "summary"]));
  const toolNames = toolRows.length
    ? toolRows.map((row) => firstRecordString(row, ["name", "tool_name", "title", "id"]) || "").filter(Boolean)
    : asArray(payloadValue(result, ["discovered_tools", "tools", "results", "items"]))
        .map((value) => (typeof value === "string" ? value : ""))
        .filter(Boolean);

  return (
    <ToolBlock icon={<Search className="size-3.5" />} title="Tool search" status={toolStatusLabel(state, result)}>
      {allQueries.length ? <Chips items={allQueries} /> : null}
      {toolRows.length && hasDetail ? (
        <ResultList
          rows={toolRows}
          titleKeys={["name", "tool_name", "title", "id"]}
          detailKeys={["description", "summary", "detail", "label"]}
          icon={<Wrench className="size-3" />}
        />
      ) : toolNames.length ? (
        <Chips items={toolNames} />
      ) : state === "result" ? (
        <Empty text="No tools found." />
      ) : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

function ListenDetails({ args, state, result }: { args: ToolCardArgs; state: string; result: ToolCardResult }) {
  const transcript = resultText(result, ["transcript", "text"]);
  const lang = firstRecordString(result, ["detected_language", "language"]);
  const duration = payloadValue(result, ["duration_seconds", "duration"]);
  const path = firstToolArgString(args, ["file_path"]);

  return (
    <ToolBlock icon={<Mic className="size-3.5" />} title="Transcription" status={toolStatusLabel(state, result)}>
      <MetaRow
        entries={[
          { label: "File", value: path ? fileNameFromPath(path) : undefined },
          { label: "Language", value: lang },
          { label: "Duration", value: typeof duration === "number" ? `${Math.round(duration)}s` : undefined },
        ]}
      />
      {transcript ? <Quote text={transcript} /> : null}
      <CodeBlock label="Error" value={resultText(result, ["error"])} tone="error" />
    </ToolBlock>
  );
}

export function contextualToolDetails({
  toolName,
  args,
  state,
  result,
}: {
  toolName: string;
  args: ToolCardArgs;
  state: string;
  result: ToolCardResult;
}): ReactNode | null {
  const normalizedName = normalizeToolNameForDisplay(toolName);

  if (isCommandDetailTool(normalizedName)) {
    return <CommandToolDetails normalizedName={normalizedName} args={args} state={state} result={result} />;
  }

  if (normalizedName === "execute_python") {
    return <PythonToolDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "apply_patch" || normalizedName === "edit_file" || normalizedName === "create_file") {
    return <PatchToolDetails normalizedName={normalizedName} args={args} state={state} result={result} />;
  }

  if (
    normalizedName === "read_file"
    || normalizedName === "file_processor"
    || normalizedName === "view_image"
  ) {
    return <FileToolDetails normalizedName={normalizedName} args={args} state={state} result={result} />;
  }

  if (
    normalizedName === "web_search"
    || normalizedName === "search_operations_tool"
    || normalizedName === "get_operation_details_tool"
  ) {
    return <SearchToolDetails args={args} state={state} result={result} />;
  }

  if (
    normalizedName === "list_skills"
    || normalizedName === "load_skill"
    || normalizedName === "list_skill_resources"
    || normalizedName === "load_skill_resource"
  ) {
    return <SkillToolDetails normalizedName={normalizedName} args={args} state={state} result={result} />;
  }

  if (normalizedName === "write_todos" || normalizedName === "update_plan") {
    return <TodosDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_query" || normalizedName === "pod_get_records") {
    return <PodDataTableDetails normalizedName={normalizedName} args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_tables") {
    return <PodTablesDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_list_files") {
    return <PodFilesDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_read_file") {
    return <PodReadFileDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_search_files") {
    return <PodSearchFilesDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_get_file_url") {
    return <PodFileUrlDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_view_document_pages") {
    return <PodDocumentPagesDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "pod_write_record") {
    return <PodWriteRecordDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "search_tools" || normalizedName === "tool_search") {
    return <ToolSearchDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "say") {
    return <SayDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "listen") {
    return <ListenDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "spawn_subagent") {
    return <SpawnSubagentDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "interact_subagent") {
    return <SubagentInteractionDetails args={args} state={state} result={result} />;
  }

  if (normalizedName === "query_subagents") {
    return <QuerySubagentsDetails args={args} state={state} result={result} />;
  }

  if (normalizedName.startsWith("function_") || normalizedName.startsWith("agent_")) {
    return <CallableToolDetails normalizedName={normalizedName} args={args} state={state} result={result} />;
  }

  return null;
}
