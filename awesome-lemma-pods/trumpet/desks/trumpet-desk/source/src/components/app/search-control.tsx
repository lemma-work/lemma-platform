import { LemmaGlobalSearch } from "@/components/lemma/lemma-global-search";
import { appConfig } from "@/app-config";
import { client } from "@/lib/client";
import { hasPodId, runtimeConfig } from "@/lib/runtime-config";

function hashPath(pathname: string) {
  return pathname.startsWith("#") ? pathname : `#${pathname.startsWith("/") ? pathname : `/${pathname}`}`;
}

function fillTemplate(template: string, values: Record<string, unknown>) {
  return template.replace(/:([a-zA-Z0-9_]+)/g, (_, key) => encodeURIComponent(String(values[key] ?? "")));
}

export function SearchControl() {
  if (!appConfig.search) return null;
  const agentName = runtimeConfig.agentName || appConfig.agent?.agentName || "";
  return (
    <LemmaGlobalSearch
      client={client}
      podId={runtimeConfig.podId || undefined}
      enabled={hasPodId}
      triggerLabel="Search"
      tables={appConfig.search.tables.map((table) => ({
        tableName: table.tableName,
        label: table.label,
        searchFields: table.searchFields,
        displayField: table.displayField || undefined,
        subtitleField: table.subtitleField || undefined,
        href: (record) => hashPath(fillTemplate(table.hrefTemplate, { tableName: table.tableName, ...record })),
      }))}
      files={
        appConfig.search.files?.enabled
          ? {
              enabled: true,
              label: appConfig.search.files.label,
              href: (result) => hashPath(fillTemplate(appConfig.search?.files?.hrefTemplate ?? "/files?path=:path", { path: result.path })),
            }
          : undefined
      }
      assistant={
        agentName
          ? {
              agentName,
              label: "Agent",
              href: appConfig.agent?.mode === "page" ? () => hashPath("/agent") : undefined,
            }
          : undefined
      }
    />
  );
}
