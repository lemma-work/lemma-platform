/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Schema for changing an existing column.
 *
 * An ENUM column's options can be added to, and a column's foreign key
 * dropped; nothing else changes. Unknown fields are refused rather than
 * ignored, so a caller asking for a change this does not make hears so
 * instead of getting a 200 for nothing.
 */
export type UpdateColumnRequest = {
    /**
     * Drop the column's foreign key: its values stay, and stop having to name a row of the other table. A column with none is left as it is. A foreign key cannot be added to an existing column.
     */
    drop_foreign_key?: boolean;
    /**
     * Every option the ENUM column should accept: all of its current options, plus any to add. Removing an option is refused, because rows may already hold it. Sending the current options changes nothing.
     */
    options?: (Array<string> | null);
};
