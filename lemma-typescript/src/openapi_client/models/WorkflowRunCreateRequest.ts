/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { WorkflowRunAbout } from './WorkflowRunAbout.js';
/**
 * What a person may give a run they start: at most the record it is about.
 *
 * Not free-form input, which a run never takes (inputs arrive through FORM
 * nodes): the record is read by the platform, as the person starting the
 * run, and handed to the run as ``start.payload`` -- the shape a table
 * change gives it -- so one workflow serves both a table trigger and a
 * button about one row.
 */
export type WorkflowRunCreateRequest = {
    /**
     * The record this run is about. It is read as you: a record you cannot read is a 404. Its fields become `start.payload`, and `start.metadata` carries `table_name` and `record_id`.
     */
    about?: (WorkflowRunAbout | null);
};
