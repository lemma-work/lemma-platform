/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Request to update a schedule.
 */
export type UpdateScheduleRequest = {
    agent_name?: (string | null);
    /**
     * Replaces the whole config. Settings for the schedule's type: `cron`/`scheduled_at`/`timezone` for TIME, `source` for WEBHOOK, `table_name`/`operations`/`when` for DATASTORE. A TIME schedule's `cron` (5 fields) and a `scheduled_at` without an offset are read in `timezone`, an IANA name such as `Europe/Berlin`; **without `timezone` they are UTC**. The response's `next_fire_at` says when it will next fire. Any type may set `max_concurrent_runs`: at most that many of the schedule's runs in flight at once, from 1 to 1000; a fire that finds it full is skipped and recorded as skipped, not queued. DATASTORE may set `bulk_writes` and `import_writes` to `skip`, `each` or `batch`: what a bulk write or an import does to the trigger. Both default to `skip` (the changes are recorded as skipped, aggregated); the pod setting `triggers.bulk_writes_default` changes the default for `bulk_writes`.
     */
    config?: (Record<string, any> | null);
    filter_instruction?: (string | null);
    filter_output_schema?: (Record<string, any> | null);
    /**
     * DATASTORE schedules only: also fire on rows people outside the pod added to an open table. Off by default. When it fires, the run is told the row's content came from outside and is untrusted.
     */
    include_outside_rows?: (boolean | null);
    instruction?: (string | null);
    is_active?: (boolean | null);
    name?: (string | null);
    visibility?: (string | null);
    workflow_name?: (string | null);
};
