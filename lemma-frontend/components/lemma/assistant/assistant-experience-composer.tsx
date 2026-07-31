"use client";

import type { ChangeEvent, KeyboardEvent, ReactNode, RefObject } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  ArrowUp,
  Database,
  FileText,
  Plus,
  Square,
} from "@/components/ui/icons";
import type {
  AssistantControllerView,
  AssistantPendingFileRenderArgs,
  AssistantResourceMention,
  LemmaAssistantDensity,
  LemmaAssistantRadius,
} from "./assistant-types";
import type { AssistantPendingFileUpload } from "lemma-sdk/react";
import type { PlanSummaryState } from "lemma-sdk";
import { isAskUserToolName } from "lemma-sdk";
import { AssistantComposer, type AssistantSurfaceTone } from "./assistant-chrome";
import { ComposerApprovalPanel, ComposerAskUserPanel } from "./assistant-message-group";
import { PlanSummaryStrip } from "./assistant-parts";
import { assistantComposerInputClassName } from "./assistant-experience-helpers";
import type { getActiveResourceMention } from "./assistant-experience-helpers";

type ActiveResourceMention = ReturnType<typeof getActiveResourceMention>;

export interface AssistantExperienceComposerBodyProps {
  controller: AssistantControllerView;
  activePendingApprovalInvocation: Parameters<typeof ComposerApprovalPanel>[0]["invocation"] | null | undefined;
  activeResourceMention: ActiveResourceMention;
  insertResourceMention: (mention: AssistantResourceMention) => void;
  radius: LemmaAssistantRadius;
  density: LemmaAssistantDensity;
  fileInputRef: RefObject<HTMLInputElement | null>;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  draft: string;
  placeholder: string;
  isConversationBusy: boolean;
  hasPendingFileUploads: boolean;
  runtimeLabel: string | null;
  composerModelControl: ReactNode;
  onUploadSelection: (files: FileList | null) => void;
  onDraftChange: (event: ChangeEvent<HTMLTextAreaElement>) => void;
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  onUpdateDraftSelection: () => void;
  onSubmit: () => void;
}

export function AssistantExperienceComposerBody({
  controller,
  activePendingApprovalInvocation,
  activeResourceMention,
  insertResourceMention,
  radius,
  density,
  fileInputRef,
  inputRef,
  draft,
  placeholder,
  isConversationBusy,
  hasPendingFileUploads,
  runtimeLabel,
  composerModelControl,
  onUploadSelection,
  onDraftChange,
  onKeyDown,
  onUpdateDraftSelection,
  onSubmit,
}: AssistantExperienceComposerBodyProps) {
  if (activePendingApprovalInvocation) {
    if (isAskUserToolName(activePendingApprovalInvocation.toolName)) {
      return (
        <ComposerAskUserPanel
          invocation={activePendingApprovalInvocation}
          onResolveUserApproval={controller.resolveUserApproval}
        />
      );
    }
    return (
      <ComposerApprovalPanel
        invocation={activePendingApprovalInvocation}
        onResolveUserApproval={controller.resolveUserApproval}
      />
    );
  }

  return (
    <div className="min-w-0">
      {activeResourceMention && activeResourceMention.items.length > 0 ? (
        <div className="mb-2 max-h-64 overflow-y-auto rounded-lg border border-[var(--row-border)] bg-[var(--surface-overlay)] p-1.5 shadow-[var(--shadow-sm)]">
          <div className="px-2 pb-1 pt-0.5 type-eyebrow-medium">
            Refer to
          </div>
          {activeResourceMention.items.map((mention) => (
            <button
              key={mention.id}
              type="button"
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => insertResourceMention(mention)}
              className="lemma-assistant-resource-mention-button flex w-full min-w-0 items-center gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors hover:bg-[var(--row-bg)]"
            >
              <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-[var(--row-border)] bg-[var(--card-bg)] text-[var(--text-tertiary)]">
                {mention.kind === "table" ? <Database className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate font-medium text-[var(--text-primary)]">{mention.label}</span>
                <span className="block truncate text-xs text-[var(--text-tertiary)]">
                  {mention.detail || mention.insertText}
                </span>
              </span>
            </button>
          ))}
        </div>
      ) : null}
      <div className={assistantComposerInputClassName(radius, density)}>
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => { onUploadSelection(event.target.files); }}
      />
      <Textarea
        ref={inputRef}
        value={draft}
        onChange={onDraftChange}
        onKeyDown={onKeyDown}
        onKeyUp={onUpdateDraftSelection}
        onClick={onUpdateDraftSelection}
        onSelect={onUpdateDraftSelection}
        placeholder={placeholder}
        disableFocusRing
        className={cn(
          "assistant-composer-textarea min-w-0 w-full resize-none !transform-none !rounded-none !border-0 !bg-transparent px-0 text-sm leading-6 !shadow-none outline-none placeholder:text-[var(--text-tertiary)] hover:!transform-none hover:!border-0 hover:!bg-transparent hover:!shadow-none focus:!border-0 focus:!bg-transparent focus:outline-none focus:ring-0 focus-visible:!transform-none focus-visible:!border-0 focus-visible:!bg-transparent focus-visible:outline-none focus-visible:ring-0 focus-visible:!shadow-none",
          density === "compact" ? "max-h-28 h-10 min-h-10 py-1" : "max-h-[220px] h-12 min-h-12 py-1",
        )}
        rows={1}
        disabled={isConversationBusy}
      />
      <div className="flex min-w-0 items-center gap-2">
        <Button
          type="button"
          variant="ghost"
          size="icon"
          onClick={() => fileInputRef.current?.click()}
          disabled={isConversationBusy || controller.isUploadingFiles}
          className="size-8 shrink-0 text-[var(--text-secondary)]"
          data-disabled={isConversationBusy || controller.isUploadingFiles ? "true" : "false"}
          title="Upload files"
        >
          <Plus className="size-4" />
        </Button>
        {composerModelControl ? (
          <div className="min-w-0 shrink">
            {composerModelControl}
          </div>
        ) : runtimeLabel ? (
          <span className="inline-flex min-w-0 max-w-[18rem] items-center gap-1.5 rounded-md px-2 py-1 text-xs text-[var(--text-secondary)]">
            <span className="truncate">{runtimeLabel}</span>
          </span>
        ) : null}
        <span className="min-w-0 flex-1" />
        <Button
          type="button"
          variant={isConversationBusy ? "destructive" : draft.trim() || hasPendingFileUploads ? "primary" : "ghost"}
          size="icon"
          onClick={isConversationBusy ? controller.stop : () => { onSubmit(); }}
          disabled={!isConversationBusy && !draft.trim() && !hasPendingFileUploads}
          className={cn(
            "size-9 shrink-0 rounded-full",
            !isConversationBusy && (draft.trim() || hasPendingFileUploads) && "button-primary-gradient",
          )}
          data-state={isConversationBusy ? "busy" : draft.trim() || hasPendingFileUploads ? "ready" : "idle"}
          aria-label={isConversationBusy ? "Stop generating" : "Send message"}
          title={isConversationBusy ? "Stop generating" : "Send message"}
        >
          {isConversationBusy ? <Square className="size-3" /> : <ArrowUp className="size-4" />}
        </Button>
      </div>
      </div>
    </div>
  );
}

export interface AssistantExperienceComposerProps extends AssistantExperienceComposerBodyProps {
  composerTone: AssistantSurfaceTone;
  composerWidthClassName?: string;
  planSummary: PlanSummaryState | null;
  isPlanHidden: boolean;
  onShowPlan: () => void;
  onHidePlan: () => void;
  hasComposerStatus: boolean;
  composerStatus: ReactNode;
  pendingFileUploads: AssistantPendingFileUpload[];
  renderPendingFile: (args: AssistantPendingFileRenderArgs) => ReactNode;
}

export function AssistantExperienceComposer({
  composerTone,
  composerWidthClassName,
  planSummary,
  isPlanHidden,
  onShowPlan,
  onHidePlan,
  hasComposerStatus,
  composerStatus,
  pendingFileUploads,
  renderPendingFile,
  ...bodyProps
}: AssistantExperienceComposerProps) {
  const { controller, radius, density } = bodyProps;
  return (
    <AssistantComposer
      tone={composerTone}
      radius={radius}
      compact={density === "compact"}
      innerClassName={composerWidthClassName}
      floating={planSummary ? (
        isPlanHidden ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onShowPlan}
            className="h-7 px-2 text-xs"
          >
            Show plan ({planSummary.completedCount}/{planSummary.steps.length})
          </Button>
        ) : (
          <PlanSummaryStrip
            plan={planSummary}
            onHide={onHidePlan}
          />
        )
      ) : undefined}
      status={hasComposerStatus ? composerStatus : undefined}
      pendingFiles={pendingFileUploads.length > 0 ? (
        <>
          {pendingFileUploads.map((upload) => {
            return (
              <div key={upload.key}>
                {renderPendingFile({
                  file: upload.file,
                  status: upload.status,
                  path: upload.path,
                  error: upload.error,
                  remove: () => controller.removePendingFile(upload.key),
                })}
              </div>
            );
          })}
        </>
      ) : undefined}
    >
      <AssistantExperienceComposerBody {...bodyProps} />
    </AssistantComposer>
  );
}
