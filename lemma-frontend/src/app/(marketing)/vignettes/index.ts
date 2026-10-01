import type { ComponentType } from "react";
import { PagesVisualize } from "./pages-visualize";
import { PagesThreadVsPage } from "./pages-thread-vs-page";
import { PagesAskForChange } from "./pages-ask-for-change";
import { PagesLiveView } from "./pages-live-view";
import { PagesWidgetWhatif } from "./pages-widget-whatif";
import { PagesCommentHandoff } from "./pages-comment-handoff";
import { TablesFromASentence } from "./tables-from-a-sentence";
import { TablesViewReadsRows } from "./tables-view-reads-rows";
import { TablesSaltboxSigns } from "./tables-saltbox-signs";
import { TablesOwnRows } from "./tables-own-rows";
import { TablesAskRows } from "./tables-ask-rows";
import { WorkflowsHandoff } from "./workflows-handoff";
import { WorkflowsSteps } from "./workflows-steps";
import { WorkflowsStarts } from "./workflows-starts";
import { WorkflowsAnswerInChat } from "./workflows-answer-in-chat";
import { WorkflowsBuiltByAsking } from "./workflows-built-by-asking";
import { AppsOutgrewChat } from "./apps-outgrew-chat";
import { AppsJuneNormalize } from "./apps-june-normalize";
import { AppsRulesBuiltIn } from "./apps-rules-built-in";
import { AppsTwoViewers } from "./apps-two-viewers";
import { AppsLiveRow } from "./apps-live-row";
import { AppsWidgetToApp } from "./apps-widget-to-app";
import { MemoryChatEnds } from "./memory-chat-ends";
import { MemoryNextPerson } from "./memory-next-person";
import { MemoryChecksNotes } from "./memory-checks-notes";
import { MemoryFix } from "./memory-fix";
import { MemoryNoteAndSkill } from "./memory-note-and-skill";
import { ChannelsThreeApps } from "./channels-three-apps";
import { ChannelsWhoIsAsking } from "./channels-who-is-asking";
import { ChannelsSlackWorking } from "./channels-slack-working";
import { ChannelsApproval } from "./channels-approval";
import { ChannelsStepFindsPerson } from "./channels-step-finds-person";
import { ChannelsRoundUp } from "./channels-round-up";
import { HomeSpaceFills } from "./home-space-fills";

/** Every vignette by id, the id a product page's section names it by. Each
 *  is built and previewed on its own at /product/vignettes/<id> (development
 *  only), and reviewed beat by beat before it is listed here. */
export const VIGNETTES: Record<string, ComponentType> = {
    "pages-visualize": PagesVisualize,
    "pages-thread-vs-page": PagesThreadVsPage,
    "pages-ask-for-change": PagesAskForChange,
    "pages-live-view": PagesLiveView,
    "pages-widget-whatif": PagesWidgetWhatif,
    "pages-comment-handoff": PagesCommentHandoff,
    "tables-from-a-sentence": TablesFromASentence,
    "tables-view-reads-rows": TablesViewReadsRows,
    "tables-saltbox-signs": TablesSaltboxSigns,
    "tables-own-rows": TablesOwnRows,
    "tables-ask-rows": TablesAskRows,
    "workflows-handoff": WorkflowsHandoff,
    "workflows-steps": WorkflowsSteps,
    "workflows-starts": WorkflowsStarts,
    "workflows-answer-in-chat": WorkflowsAnswerInChat,
    "workflows-built-by-asking": WorkflowsBuiltByAsking,
    "apps-outgrew-chat": AppsOutgrewChat,
    "apps-june-normalize": AppsJuneNormalize,
    "apps-rules-built-in": AppsRulesBuiltIn,
    "apps-two-viewers": AppsTwoViewers,
    "apps-live-row": AppsLiveRow,
    "apps-widget-to-app": AppsWidgetToApp,
    "memory-chat-ends": MemoryChatEnds,
    "memory-next-person": MemoryNextPerson,
    "memory-checks-notes": MemoryChecksNotes,
    "memory-fix": MemoryFix,
    "memory-note-and-skill": MemoryNoteAndSkill,
    "channels-three-apps": ChannelsThreeApps,
    "channels-who-is-asking": ChannelsWhoIsAsking,
    "channels-slack-working": ChannelsSlackWorking,
    "channels-approval": ChannelsApproval,
    "channels-step-finds-person": ChannelsStepFindsPerson,
    "channels-round-up": ChannelsRoundUp,
    "home-space-fills": HomeSpaceFills,
};
