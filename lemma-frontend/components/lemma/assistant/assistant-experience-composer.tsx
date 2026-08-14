"use client";

import type { ChangeEvent, KeyboardEvent, ReactNode, RefObject } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Table,
  FileText,
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
import { Composer } from "@/components/shared/composer";
import { ComposerApprovalPanel, ComposerAskUserPanel } from "./assistant-message-group";
import { PlanSummaryStrip } from "./assistant-parts";
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
                {mention.kind === "table" ? <Table className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
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
      <input
        ref={fileInputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(event) => { onUploadSelection(event.target.files); }}
      />
      <Composer
        inputRef={inputRef}
        draft={draft}
        onDraftChange={onDraftChange}
        onKeyDown={onKeyDown}
        onSelectionChange={onUpdateDraftSelection}
        onSubmit={onSubmit}
        placeholder={placeholder}
        isBusy={isConversationBusy}
        hasAttachments={hasPendingFileUploads}
        onStop={controller.stop}
        onAttach={() => fileInputRef.current?.click()}
        onDropFiles={(files) => { onUploadSelection(files); }}
        isAttaching={controller.isUploadingFiles}
        density={density === 'compact' ? 'tight' : 'roomy'}
        className={cn(radius === 'none' && 'rounded-none')}
        controls={composerModelControl ?? (runtimeLabel ? (
          <span className="truncate px-2 py-1 text-xs text-[var(--text-secondary)]">{runtimeLabel}</span>
        ) : null)}
      />
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
            variant="secondary"
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
