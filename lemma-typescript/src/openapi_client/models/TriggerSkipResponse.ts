/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TriggerSkipReason } from './TriggerSkipReason.js';
/**
 * Changes or fires the trigger let pass without firing, aggregated.
 *
 * One row stands for every change of one delivered page skipped for one
 * reason: a bulk write of thousands of rows is a handful of rows here.
 */
export type TriggerSkipResponse = {
    at: string;
    /**
     * How many changes or fires this row stands for.
     */
    count: number;
    /**
     * Position of the first skipped change or fire.
     */
    first: string;
    id: string;
    /**
     * Position of the last skipped change or fire.
     */
    last: string;
    /**
     * `bulk_write` / `import_write`: the trigger's write mode for that intent is `skip`. `fire_cap`: the trigger reached its daily fire ceiling and paused. `lag_exceeded`: the trigger fell further behind its table than the change log waits for a subscriber (24 hours by default), and these changes were passed over.
     */
    reason: TriggerSkipReason;
};
