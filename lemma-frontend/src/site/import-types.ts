export type ExportStatus = "QUEUED" | "EXPORTING" | "READY" | "FAILED";
export type ImportStatus =
    | "QUEUED"
    | "FETCHING"
    | "PLANNING"
    | "AWAITING_CONFIRMATION"
    | "APPLYING"
    | "COMPLETED"
    | "FAILED"
    | "CANCELLED"
    | "PARTIALLY_CANCELLED";
export type PublishStatus =
    | "QUEUED"
    | "EXPORTING"
    | "PUBLISHING"
    | "COMPLETED"
    | "FAILED";
export type StepAction = "CREATE" | "UPDATE" | "SKIP";
export type StepStatus = "PENDING" | "RUNNING" | "DONE" | "FAILED" | "SKIPPED";
export type BundleSourceKind = "URL" | "GITHUB";
export type PublishMode = "CREATE" | "UPDATE";

export interface BundleProgress {
    done: number;
    total: number;
}

export interface ExportStatusResponse {
    export_id: string;
    status: ExportStatus;
    progress: BundleProgress;
    bundle_filename: string | null;
    download_url: string | null;
    expires_at: string | null;
    warnings: string[];
    error: string | null;
}

export interface PlanStep {
    index: number;
    kind: string;
    name: string;
    action: StepAction;
    destructive: boolean;
    detail: Record<string, unknown>;
    status: StepStatus;
    error: string | null;
}

export interface VariableSpec {
    name: string;
    kind: string;
    description: string | null;
    required: boolean;
    default: string | null;
    /** For `account`-kind variables: the connector, e.g. "slack". */
    connector?: string | null;
    /** For `account`-kind variables: which of the connector's kinds the source
     * install used ("composio", "http", "mcp", ...), so the picker selects
     * or creates an account of the same kind rather than any account for that
     * connector. */
    connector_kind?: string | null;
}

export interface ImportPlan {
    format_version: number;
    bundle_name: string | null;
    steps: PlanStep[];
    variables: VariableSpec[];
    warnings: string[];
    has_destructive_steps: boolean;
}

export interface ImportStatusResponse {
    import_id: string;
    pod_id: string;
    status: ImportStatus;
    source_kind: string;
    plan: ImportPlan | null;
    progress: BundleProgress;
    events_url: string;
    error: string | null;
    error_code: string | null;
    retryable: boolean;
    warnings: string[];
}

export interface UploadResponse {
    url: string;
    expires_at: string;
}

export interface PublishStatusResponse {
    publish_id: string;
    pod_id: string;
    status: PublishStatus;
    repo_name: string;
    mode: PublishMode;
    private: boolean;
    account_id: string | null;
    repo_url: string | null;
    progress: BundleProgress;
    events_url: string;
    error: string | null;
    error_code: string | null;
    retryable: boolean;
    warnings: string[];
}
