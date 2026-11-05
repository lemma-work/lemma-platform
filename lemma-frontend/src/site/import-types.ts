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

/** A value the bundle asks for at install time. `pod_member` fills itself in
 *  with whoever installs; `free` is a name. A bundle's connectors are not
 *  variables: they come with their own choices, in `ImportPlan.connectors`. */
export interface VariableSpec {
    name: string;
    kind: "pod_member" | "free";
    description: string | null;
    required: boolean;
    default: string | null;
}

export type ConnectorPlanStatus =
    | "READY"
    | "NEEDS_ACCOUNT"
    | "NEEDS_INSTALLATION"
    | "EXISTS"
    | "CONFLICT";

export interface InstallationOption {
    auth_config_id: string;
    name: string;
    is_default: boolean;
}

/** One of the installer's own accounts, shareable for a SHARED connector. */
export interface AccountOption {
    account_id: string;
    auth_config_id: string;
    label: string | null;
}

/** A pod connector the bundle declares (`connectors/<slug>/`), and what the
 *  installer may choose for it in their own organization. A bundle carries
 *  the catalog connector and the mode only — never an installation or an
 *  account, which are chosen here. */
export interface ConnectorPlan {
    slug: string;
    connector: string;
    mode: "PER_MEMBER" | "SHARED";
    required: boolean;
    purpose: string | null;
    visibility: "POD" | "RESTRICTED";
    status: ConnectorPlanStatus;
    installations: InstallationOption[];
    /** The installation used unless another is picked: the org's default. */
    auth_config_id: string | null;
    can_create_installation: boolean;
    account_options: AccountOption[];
    used_by: string[];
    message: string | null;
}

/** What the apply request says for one connector, by slug. */
export interface ConnectorChoice {
    auth_config_id?: string;
    create_installation?: boolean;
    share_account_id?: string;
}

export interface ImportPlan {
    format_version: number;
    bundle_name: string | null;
    steps: PlanStep[];
    variables: VariableSpec[];
    warnings: string[];
    has_destructive_steps: boolean;
    /** Absent from a plan made before bundles carried connectors. */
    connectors?: ConnectorPlan[];
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
    /** Where to connect the GitHub account the pod's `github` connector
     *  needs, when the job failed for want of one. */
    connect_url: string | null;
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
    repo_url: string | null;
    progress: BundleProgress;
    events_url: string;
    error: string | null;
    error_code: string | null;
    /** As on an import: where to connect the GitHub account a publish
     *  needed. */
    connect_url: string | null;
    retryable: boolean;
    warnings: string[];
}
