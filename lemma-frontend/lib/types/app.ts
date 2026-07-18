// App runtime types.

export interface AppConfig {
    id: string;
    podId: string;
    name: string;
    pages: AppPageRef[];
    createdAt: string;
    updatedAt: string;
}

export interface AppPageRef {
    id?: string;
    slug: string;
    title: string;
    appName?: string;
    description?: string | null;
    url?: string;
    icon?: string;
    order: number;
    path: string;
    visibility?: string | null;
    allowed_actions?: string[] | null;
}

export interface AppPage {
    id?: string;
    slug: string;
    podId: string;
    title: string;
    url?: string;
    icon?: string;
    order: number;
    createdAt: string;
    updatedAt: string;
    visibility?: string | null;
    allowed_actions?: string[] | null;
}

// Shared data filtering helpers reused by record/data views.
export interface SystemFieldConfig {
    enabled: boolean;
    label?: string;
    displayFormat?: 'absolute' | 'relative';
}

export interface FilterRule {
    field: string;
    operator: 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'contains' | 'startsWith' | 'endsWith' | 'in';
    value: unknown;
}

export interface SortRule {
    field: string;
    direction: 'asc' | 'desc';
}

export interface DataSourceConfig {
    type: 'function' | 'flow';
    id: string;
    input?: Record<string, unknown>;
    injectRecordId?: {
        enabled: boolean;
        inputField: string;
    };
}
