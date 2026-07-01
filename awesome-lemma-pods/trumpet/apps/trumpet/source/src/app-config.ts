export type NavigationType = "single-page" | "sidebar" | "topbar";
export type AgentChatMode = "page" | "popup" | "right-sidebar";

export interface DeskAppConfig {
  title: string;
  navigation: NavigationType;
  agent: { agentName: string; mode: AgentChatMode } | null;
  membersPage: boolean;
  search: {
    tables: Array<{
      tableName: string;
      label: string;
      searchFields: string[];
      displayField: string;
      subtitleField: string;
      hrefTemplate: string;
    }>;
    files: { enabled: boolean; label: string; hrefTemplate: string } | null;
  } | null;
  themeToggle: boolean;
}

export const appConfig: DeskAppConfig = {
  "title": "Trumpet",
  "navigation": "single-page",
  "agent": {
    "agentName": "mr-toot",
    "mode": "popup"
  },
  "membersPage": false,
  "search": null,
  "themeToggle": false
};
