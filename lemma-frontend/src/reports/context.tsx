"use client";

import { createContext, useContext } from "react";

/** What a shared chat's lines need to report and block: the Lemma's name, as
 *  the dialogs say it, and whether the reader may stop it answering a
 *  contact. Given by the chat's page; a line drawn without one (the sample,
 *  a test) still reports and blocks, and calls the Lemma "this Lemma". */
export interface ReportPlace {
    lemma: string;
    mayStopAnswering: boolean;
}

export const ReportPlaceContext = createContext<ReportPlace>({ lemma: "this Lemma", mayStopAnswering: false });

export function useReportPlace(): ReportPlace {
    return useContext(ReportPlaceContext);
}
