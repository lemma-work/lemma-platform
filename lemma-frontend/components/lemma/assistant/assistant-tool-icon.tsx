import {
  Bot,
  BookOpen,
  CheckCircle2,
  CircleHelp,
  Code2,
  Database,
  FilePenLine,
  FileSearch,
  FileText,
  Folder,
  Globe2,
  Image as ImageIcon,
  Link2,
  ListChecks,
  MessageSquare,
  Mic,
  PanelsTopLeft,
  Settings2,
  ShieldCheck,
  SquareTerminal,
  Table2,
  Users,
  Volume2,
  Wrench,
  type LucideIcon,
  type LucideProps,
} from "lucide-react";
import { normalizeToolNameForDisplay } from "./assistant-format";

export type ToolIconKind =
  | "agent"
  | "approval"
  | "audio-input"
  | "audio-output"
  | "code"
  | "complete"
  | "data"
  | "display"
  | "file-edit"
  | "file-read"
  | "file-search"
  | "files"
  | "image"
  | "link"
  | "message"
  | "plan"
  | "process"
  | "question"
  | "skills"
  | "table"
  | "terminal"
  | "users"
  | "web"
  | "tool";

const TOOL_ICON_KINDS: Record<string, ToolIconKind> = {
  apply_patch: "file-edit",
  ask_user: "question",
  command_execution: "terminal",
  create_file: "file-edit",
  display_resource: "display",
  edit_file: "file-edit",
  exec_command: "terminal",
  execute_command: "terminal",
  execute_python: "code",
  file_processor: "file-read",
  final_answer: "complete",
  final_result: "complete",
  grep: "file-search",
  interact_subagent: "message",
  listen: "audio-input",
  list_processes: "process",
  list_skill_resources: "skills",
  list_skills: "skills",
  load_skill: "skills",
  load_skill_resource: "skills",
  manage_process: "process",
  pod_get_file_url: "link",
  pod_get_records: "data",
  pod_list_files: "files",
  pod_query: "data",
  pod_read_file: "file-read",
  pod_search_files: "file-search",
  pod_tables: "table",
  pod_view_document_pages: "file-read",
  pod_write_file: "file-edit",
  pod_write_record: "data",
  query_subagents: "users",
  read_file: "file-read",
  request_approval: "approval",
  say: "audio-output",
  search_query: "web",
  search_tools: "tool",
  spawn_subagent: "agent",
  terminate_process: "process",
  tool_search: "tool",
  update_plan: "plan",
  view_image: "image",
  web_search: "web",
  write_stdin: "terminal",
  write_todos: "plan",
};

const ICONS: Record<ToolIconKind, LucideIcon> = {
  agent: Bot,
  approval: ShieldCheck,
  "audio-input": Mic,
  "audio-output": Volume2,
  code: Code2,
  complete: CheckCircle2,
  data: Database,
  display: PanelsTopLeft,
  "file-edit": FilePenLine,
  "file-read": FileText,
  "file-search": FileSearch,
  files: Folder,
  image: ImageIcon,
  link: Link2,
  message: MessageSquare,
  plan: ListChecks,
  process: Settings2,
  question: CircleHelp,
  skills: BookOpen,
  table: Table2,
  terminal: SquareTerminal,
  users: Users,
  web: Globe2,
  tool: Wrench,
};

export function toolIconKind(toolName: string): ToolIconKind {
  return TOOL_ICON_KINDS[normalizeToolNameForDisplay(toolName)] || "tool";
}

export function ToolCallIcon({ toolName, ...props }: { toolName: string } & LucideProps) {
  const Icon = ICONS[toolIconKind(toolName)];
  return <Icon aria-hidden="true" strokeWidth={1.8} {...props} />;
}
