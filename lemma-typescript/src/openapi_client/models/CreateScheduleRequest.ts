/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ScheduleType } from './ScheduleType.js';
/**
 * Request to create a pod schedule.
 */
export type CreateScheduleRequest = {
    /**
     * Deprecated -- do not send; name `pod_connector` instead. Still accepted, as the pod connector that serves this account to you.
     * @deprecated
     */
    account_id?: (string | null);
    /**
     * Pod agent to wake, by name. Pass 'POD_DEFAULT' (or 'pod_default') to wake the pod's default assistant, which has no name of its own.
     */
    agent_name?: (string | null);
    /**
     * Settings for the schedule's type: `cron`/`scheduled_at`/`timezone` for TIME, `source` for WEBHOOK, `table_name`/`operations`/`when` for DATASTORE. A TIME schedule's `cron` (5 fields) and a `scheduled_at` without an offset are read in `timezone`, an IANA name such as `Europe/Berlin`; **without `timezone` they are UTC**. The response's `next_fire_at` says when it will next fire. Any type may set `max_concurrent_runs`: at most that many of the schedule's runs in flight at once, from 1 to 1000; a fire that finds it full is skipped and recorded as skipped, not queued. DATASTORE may set `bulk_writes` and `import_writes` to `skip`, `each` or `batch`: what a bulk write or an import does to the trigger. Both default to `skip` (the changes are recorded as skipped, aggregated); the pod setting `triggers.bulk_writes_default` changes the default for `bulk_writes`.
     */
    config?: Record<string, any>;
    /**
     * Connector trigger id for agent WEBHOOK schedules. Do not provide this for workflow schedules; workflow WEBHOOK schedules derive it from the workflow start configuration.
     */
    connector_trigger_id?: (string | null);
    /**
     * Optional schedule-level LLM filter instruction. Filters belong to the schedule, not the workflow start.
     */
    filter_instruction?: (string | null);
    /**
     * Optional schema for the schedule-level filter output. Filters belong to the schedule, not the workflow start.
     */
    filter_output_schema?: (Record<string, any> | null);
    /**
     * DATASTORE schedules only: also fire on rows people outside the pod added to an open table. Off by default. When it fires, the run is told the row's content came from outside and is untrusted.
     */
    include_outside_rows?: boolean;
    /**
     * What the target should do when this fires, in your own words. Reaches an agent as the run's conversation instructions, layered after the agent's own. Required when targeting the default assistant, which has no standing instruction to fall back on. Distinct from filter_instruction, which decides whether to fire.
     */
    instruction?: (string | null);
    /**
     * Stable pod-scoped schedule name used for import/export upserts.
     */
    name?: (string | null);
    /**
     * The pod connector, by slug, a WEBHOOK schedule listens through -- for a connector trigger or an MCP server's event. The account it means is the connector's decision: your own for a per-member connector, the shared one for a shared connector. It is resolved again on every fire and renewal, as the schedule's owner.
     */
    pod_connector?: (string | null);
    schedule_type: ScheduleType;
    visibility?: (string | null);
    workflow_name?: (string | null);
};
