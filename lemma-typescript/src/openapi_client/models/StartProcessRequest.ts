/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TerminalSizeModel } from './TerminalSizeModel.js';
export type StartProcessRequest = {
    command: string;
    /**
     * Inside the machine's home: absolute, `~/...`, or relative to ~/lemma. Defaults to ~/lemma.
     */
    cwd?: (string | null);
    env?: Record<string, string>;
    /**
     * With `service`: start it again after a pause.
     */
    restart_on_resume?: boolean;
    /**
     * Stop it when this run ends (pod machines tag agent work this way).
     */
    run_id?: (string | null);
    /**
     * Run as a named service: no lifetime cap, not stopped when the run that started it ends, listed under Processes as a service.
     */
    service?: (string | null);
    /**
     * How long it may run. Capped at the machine's process lifetime.
     */
    timeout_seconds?: (number | null);
    /**
     * Run in a terminal of this size (for a shell).
     */
    tty?: (TerminalSizeModel | null);
};
