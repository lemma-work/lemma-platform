export type DisplayResourceType =
    | 'FILE'
    | 'TABLE'
    | 'AGENT'
    | 'FUNCTION'
    | 'WORKFLOW'
    | 'APP'
    | 'SCHEDULE'
    | 'WIDGET';

export interface DisplayResourceRequest {
    type: DisplayResourceType;
    name?: string;
    path?: string;
    publicUrl?: string;
    content?: string;
    loadingMessages: string[];
    filters?: DisplayResourceTableFilter[];
    query?: string;
}

export interface DisplayResourceTableFilter {
    field: string;
    op: string;
    value?: unknown;
}

export interface DisplayResourceInvocation {
    toolCallId: string;
    request: DisplayResourceRequest;
}

interface ToolInvocationLike {
    toolCallId?: string;
    toolName?: string;
    args?: unknown;
}

interface ConversationMessageLike {
    content?: unknown;
    metadata?: unknown;
    message_metadata?: unknown;
    role?: string;
    kind?: unknown;
    tool_name?: unknown;
    tool_call_id?: unknown;
    tool_args?: unknown;
    tool_result?: unknown;
}

function asRecord(value: unknown): Record<string, unknown> {
    return value && typeof value === 'object' && !Array.isArray(value)
        ? value as Record<string, unknown>
        : {};
}

function asString(value: unknown): string | undefined {
    return typeof value === 'string' && value.trim().length > 0 ? value.trim() : undefined;
}

function asStringArray(value: unknown): string[] {
    return Array.isArray(value)
        ? value.map((entry) => (typeof entry === 'string' ? entry.trim() : '')).filter(Boolean)
        : [];
}

function normalizeResourceType(value: unknown): DisplayResourceType | null {
    if (typeof value !== 'string') return null;
    const normalized = value.trim().toUpperCase();
    const allowed: DisplayResourceType[] = [
        'FILE',
        'TABLE',
        'AGENT',
        'FUNCTION',
        'WORKFLOW',
        'APP',
        'SCHEDULE',
        'WIDGET',
    ];
    return allowed.includes(normalized as DisplayResourceType)
        ? normalized as DisplayResourceType
        : null;
}

export function isDisplayResourceToolName(toolName: unknown): boolean {
    if (typeof toolName !== 'string') return false;
    const normalized = toolName.trim().toLowerCase().replace(/[.:]/g, '_');
    return normalized === 'display_resource'
        || normalized === 'lemma_display_resource'
        || normalized.endsWith('_display_resource');
}

export function extractDisplayResourceRequest(value: unknown): DisplayResourceRequest | null {
    const args = asRecord(value);
    const request = Object.keys(asRecord(args.request)).length > 0 ? asRecord(args.request) : args;
    const type = normalizeResourceType(request.type);
    if (!type) return null;

    const filters = Array.isArray(request.filters)
        ? request.filters.filter(isDisplayResourceTableFilter)
        : undefined;

    return {
        type,
        name: asString(request.name),
        path: asString(request.path),
        publicUrl: asString(request.public_url ?? request.publicUrl),
        content: asString(request.content),
        loadingMessages: asStringArray(request.loading_messages ?? request.loadingMessages).slice(0, 4),
        filters,
        query: asString(request.query),
    };
}

export function extractDisplayResourceFromInvocation(
    invocation: ToolInvocationLike,
): DisplayResourceInvocation | null {
    if (!isDisplayResourceToolName(invocation.toolName)) return null;
    if (!invocation.toolCallId) return null;
    const request = extractDisplayResourceRequest(invocation.args);
    if (!request) return null;
    return {
        toolCallId: invocation.toolCallId,
        request,
    };
}

function rawToolCallInput(message: ConversationMessageLike): {
    toolCallId?: string;
    toolName?: string;
    args?: unknown;
} | null {
    const metadata = asRecord(message.metadata);
    const messageMetadata = asRecord(message.message_metadata);
    // Flat message shape: tool fields live at the top level of the message.
    const type = (typeof message.kind === 'string' ? message.kind : undefined)
        ?? metadata.message_type ?? messageMetadata.message_type;
    const hasArgs = typeof message.tool_args !== 'undefined'
        || 'args' in metadata || 'args' in messageMetadata;

    if (type !== 'tool_call' && !hasArgs) return null;

    const toolCallId = asString(
        message.tool_call_id
        ?? metadata.tool_call_id
        ?? messageMetadata.tool_call_id,
    );
    const toolName = asString(
        message.tool_name
        ?? metadata.tool_name
        ?? messageMetadata.tool_name,
    );
    const args =
        message.tool_args
        ?? metadata.args
        ?? metadata.tool_input
        ?? messageMetadata.args
        ?? messageMetadata.tool_input;

    if (!toolCallId || !toolName) return null;
    return { toolCallId, toolName, args };
}

export function findDisplayResourceInMessages(
    messages: ConversationMessageLike[],
    toolCallId: string,
): DisplayResourceInvocation | null {
    let invocation: DisplayResourceInvocation | null = null;
    // The backend serves an inline-content widget at a URL returned in the tool
    // *result* (tool_args holds the content; tool_result holds the url). Prefer
    // that URL so the widget embeds the backend-served, config-injected page —
    // the same portable source fragment used when it is promoted to an app.
    let resultUrl: string | undefined;
    for (const message of messages) {
        const messageToolCallId = asString(
            message.tool_call_id
            ?? asRecord(message.metadata).tool_call_id
            ?? asRecord(message.message_metadata).tool_call_id,
        );
        if (messageToolCallId !== toolCallId) continue;

        if (!invocation) {
            const raw = rawToolCallInput(message);
            if (raw && raw.toolCallId === toolCallId) {
                invocation = extractDisplayResourceFromInvocation(raw);
            }
        }
        const url = asString(asRecord(message.tool_result).url);
        if (url) resultUrl = url;
    }

    if (invocation && resultUrl && !invocation.request.publicUrl) {
        invocation.request = { ...invocation.request, publicUrl: resultUrl };
    }
    return invocation;
}

const ASSISTANT_CONVERSATION_PARAM = 'assistantConversationId';

function appendQueryParams(baseHref: string, params: Record<string, string | null | undefined>): string {
    const urlParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (!value) return;
        urlParams.set(key, value);
    });
    const query = urlParams.toString();
    if (!query) return baseHref;
    const separator = baseHref.includes('?') ? '&' : '?';
    return `${baseHref}${separator}${query}`;
}

function appendAssistantConversation(
    baseHref: string,
    conversationId?: string | null,
): string {
    return appendQueryParams(baseHref, {
        [ASSISTANT_CONVERSATION_PARAM]: conversationId,
    });
}

function appendToolContext(
    baseHref: string,
    conversationId?: string | null,
    toolCallId?: string | null,
): string {
    return appendQueryParams(baseHref, {
        toolCallId,
        [ASSISTANT_CONVERSATION_PARAM]: conversationId,
    });
}

function appendRepeatedQueryParams(
    baseHref: string,
    params: Array<[string, string]>,
): string {
    if (params.length === 0) return baseHref;

    const [basePath, existingQuery = ''] = baseHref.split('?');
    const urlParams = new URLSearchParams(existingQuery);
    params.forEach(([key, value]) => {
        urlParams.append(key, value);
    });

    const query = urlParams.toString();
    return query ? `${basePath}?${query}` : basePath;
}

function normalizePodFilePath(path: string): string {
    const normalized = path.replace(/\\/g, '/').replace(/\/+/g, '/').trim();
    const withLeadingSlash = normalized.startsWith('/') ? normalized : `/${normalized}`;
    if (withLeadingSlash === '/pod') return '/';
    if (withLeadingSlash.startsWith('/pod/')) return withLeadingSlash.slice('/pod'.length) || '/';
    return withLeadingSlash;
}

function getParentPath(path: string): string | null {
    const normalized = path.replace(/\/+$/g, '');
    const parts = normalized.split('/').filter(Boolean);
    if (parts.length <= 1) return null;
    return `/${parts.slice(0, -1).join('/')}`;
}

function buildFileResourceHref(podBase: string, path?: string, conversationId?: string | null): string {
    if (!path) return appendAssistantConversation(`${podBase}/files`, conversationId);

    const filePath = normalizePodFilePath(path);
    const params = new URLSearchParams();
    const folderPath = getParentPath(filePath);
    if (folderPath) params.set('folder', folderPath);
    params.set('file', filePath);
    if (conversationId) params.set(ASSISTANT_CONVERSATION_PARAM, conversationId);
    return `${podBase}/files?${params.toString()}`;
}

function isDisplayResourceTableFilter(value: unknown): value is DisplayResourceTableFilter {
    const record = asRecord(value);
    return typeof record.field === 'string'
        && record.field.trim().length > 0
        && typeof record.op === 'string'
        && record.op.trim().length > 0;
}

function serializeTableFilter(filter: DisplayResourceTableFilter): string {
    const payload: DisplayResourceTableFilter = {
        field: filter.field.trim(),
        op: filter.op.trim(),
    };

    if ('value' in filter) {
        payload.value = filter.value;
    }

    return JSON.stringify(payload);
}

function buildTableResourceHref(
    podBase: string,
    request: DisplayResourceRequest,
    conversationId?: string | null,
): string | null {
    if (request.query) return null;

    const baseHref = request.name
        ? `${podBase}/data?tab=${encodeURIComponent(request.name)}`
        : `${podBase}/data`;
    const serializedFilters = (request.filters || [])
        .filter(isDisplayResourceTableFilter)
        .map((filter) => ['filter', serializeTableFilter(filter)] as [string, string]);

    return appendAssistantConversation(
        appendRepeatedQueryParams(baseHref, serializedFilters),
        conversationId,
    );
}

export function buildDisplayResourceHref({
    podId,
    request,
    conversationId,
    toolCallId,
}: {
    podId: string;
    request: DisplayResourceRequest;
    conversationId?: string | null;
    toolCallId?: string | null;
}): string | null {
    const podBase = `/pod/${encodeURIComponent(podId)}`;
    const name = request.name;

    switch (request.type) {
        case 'WIDGET': {
            // Inline-content widgets need only the tool context; the view page
            // mints a backend embed URL. External widgets carry public_url.
            // Legacy pod-file widget paths are no longer renderable.
            if (request.path) return null;
            let widgetBase = appendToolContext(`${podBase}/widgets/view`, conversationId, toolCallId);
            if (request.publicUrl) widgetBase = appendQueryParams(widgetBase, { src: request.publicUrl });
            return appendRepeatedQueryParams(
                widgetBase,
                request.loadingMessages.map((message) => ['loadingMessage', message]),
            );
        }
        case 'FILE':
            return buildFileResourceHref(podBase, request.path, conversationId);
        case 'TABLE':
            return buildTableResourceHref(podBase, request, conversationId);
        case 'AGENT':
            return appendAssistantConversation(name
                ? `${podBase}/agents/${encodeURIComponent(name)}`
                : `${podBase}/ai`, conversationId);
        case 'FUNCTION':
            return appendAssistantConversation(name
                ? `${podBase}/functions/${encodeURIComponent(name)}`
                : `${podBase}/functions`, conversationId);
        case 'WORKFLOW':
            return appendAssistantConversation(name
                ? `${podBase}/flows/${encodeURIComponent(name)}`
                : `${podBase}/flows`, conversationId);
        case 'APP':
            return appendAssistantConversation(name
                ? `${podBase}/app/view?page=${encodeURIComponent(name)}`
                : `${podBase}/app/pages`, conversationId);
        case 'SCHEDULE':
            return appendAssistantConversation(name
                ? `${podBase}/schedules?target=${encodeURIComponent(name)}`
                : `${podBase}/schedules`, conversationId);
        default:
            return null;
    }
}

export function displayResourceLabel(request: DisplayResourceRequest): string {
    const name = request.name || request.path;
    const type = request.type.charAt(0) + request.type.slice(1).toLowerCase();
    return name ? `${type}: ${name}` : `${type} list`;
}
