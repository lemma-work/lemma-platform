/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Aim } from './Aim.js';
import type { Shape } from './Shape.js';
/**
 * A measure as a `scorecard` row holds it, not yet saved.
 *
 * Other columns of a row (`kind`, `is_on`, `note`, ...) are accepted and play
 * no part in counting it.
 */
export type ScorecardMeasureSpec = {
    aim: Aim;
    /**
     * `work`, `sql`, `approvals`, `standing_work` or `open_questions`.
     */
    counter?: string;
    /**
     * The row's key.
     */
    key?: string;
    /**
     * The column naming a row when it is listed.
     */
    label_column?: (string | null);
    /**
     * The column holding a row's link.
     */
    link_column?: (string | null);
    /**
     * The measure in one sentence.
     */
    measure?: string;
    /**
     * `sql` counter only: a SELECT returning `counted` and `total`.
     */
    query?: (string | null);
    /**
     * `share`, `count`, `median` or `total`. Omitted: a share when `aim` is higher, a count when it is lower.
     */
    shape?: (Shape | null);
    target: number;
    target_label?: string;
    /**
     * One SQL boolean expression over a row.
     */
    test?: (string | null);
    /**
     * The DATE or DATETIME column that places a row in a week.
     */
    time_column?: (string | null);
    /**
     * The table with one row per unit of work.
     */
    unit_table?: (string | null);
    /**
     * Median or total: one SQL number over a row.
     */
    value?: (string | null);
    /**
     * What a count, median or total is in: `minutes`.
     */
    value_unit?: (string | null);
};
