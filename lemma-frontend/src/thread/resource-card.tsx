import { AppsIcon, FileIcon, TableIcon, AgentIcon, CodeIcon, WorkflowIcon, ClockIcon, ExternalIcon } from "@/ui/icons";
import { siteUrl } from "@/session/client";
import { FileView } from "./file-view";
import { DataView } from "./data-view";
import { WidgetView } from "./widget-view";
import { resourceHref, resourceLabel, type DisplayResource } from "./display-resource";

const RESOURCE_ICONS = { WIDGET: AppsIcon, FILE: FileIcon, TABLE: TableIcon, APP: AppsIcon, AGENT: AgentIcon, FUNCTION: CodeIcon, WORKFLOW: WorkflowIcon, SCHEDULE: ClockIcon };

/** What the agent put on screen.
 *
 *  A widget it wrote is rendered, not described — the whole point of building
 *  one is that you look at it. Everything else is a card: an app opens its tab
 *  right here, and the rest open where they live in the platform, because this
 *  app has no table browser or file viewer to send you to and pretending
 *  otherwise would be worse than a link. */
export function ResourceCard({
    resource,
    podId,
    onOpenApp,
    onOpenFile,
    onOpenTable,
    conversationId,
    toolCallId,
}: {
    resource: DisplayResource;
    podId: string;
    conversationId?: string | null;
    toolCallId?: string;
    onOpenApp?: (name: string) => void;
    onOpenFile?: (path: string) => void;
    onOpenTable?: (name: string) => void;
}) {
    const label = resourceLabel(resource);
    const ResourceIcon = RESOURCE_ICONS[resource.type];

    if (resource.type === "WIDGET") {
        return (
            <WidgetView
                podId={podId}
                conversationId={conversationId}
                toolCallId={toolCallId}
                content={resource.content}
                label={label}
            />
        );
    }

    if (resource.type === "FILE" && resource.path) {
        return <FileView podId={podId} path={resource.path} onOpenTab={onOpenFile} />;
    }

    /* A table or a query arrives with its rows one request away. Carding the
       name and the word "table" was the agent answering a question and the
       screen showing the label off the drawer. */
    if (resource.type === "TABLE" || resource.query) {
        return <DataView podId={podId} name={resource.name} sql={resource.query} onOpenTable={onOpenTable} />;
    }

    const common = (
        <>
            <span className="resource__glyph"><ResourceIcon size={22} /></span>
            <span className="resource__body">
                <span className="resource__name">{label}</span>
                <span className="resource__type">
                    {resource.type.toLowerCase()}
                    {resource.path ? " · " + resource.path : ""}
                </span>
            </span>
        </>
    );

    if (resource.type === "APP" && resource.name && onOpenApp) {
        const name = resource.name;
        return (
            <button className="resource resource--link" onClick={() => onOpenApp(name)}>
                {common}
                <span className="resource__go">Open</span>
            </button>
        );
    }

    const href = resourceHref(siteUrl(), podId, resource);
    if (!href) {
        return <div className="resource">{common}</div>;
    }

    return (
        <a className="resource resource--link" href={href} target="_blank" rel="noreferrer">
            {common}
            <span className="resource__go">Open <ExternalIcon size={14} /></span>
        </a>
    );
}
