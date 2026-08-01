"use client";

import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import type {
  AssistantControllerView,
  AssistantConversationRenderArgs,
  LemmaAssistantAppearance,
  LemmaAssistantRadius,
} from "./assistant-types";
import {
  conversationStatusDotColor,
  relativeTimeAgo,
} from "./assistant-chrome";
import {
  assistantRadiusClassName,
  assistantSidebarClassName,
} from "./assistant-experience-helpers";

export interface AssistantExperienceSidebarProps {
  controller: AssistantControllerView;
  appearance: LemmaAssistantAppearance;
  radius: LemmaAssistantRadius;
  showNewConversationButton: boolean;
  renderConversationLabel: (args: AssistantConversationRenderArgs) => ReactNode;
}

export function AssistantExperienceSidebar({
  controller,
  appearance,
  radius,
  showNewConversationButton,
  renderConversationLabel,
}: AssistantExperienceSidebarProps) {
  return (
    <aside className={assistantSidebarClassName(appearance)}>
      <div className="border-b border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--text-primary)]">Conversations</div>
            <div className="mt-0.5 text-xs text-[var(--text-secondary)]">
              {controller.conversations.length} total
            </div>
          </div>
          {showNewConversationButton ? (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={controller.clearMessages}
              className="h-8 px-3 text-sm"
            >
              New
            </Button>
          ) : null}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
        {controller.conversations.map((conversation) => {
          const isActive = conversation.id === controller.activeConversationId;
          return (
            <button
              key={conversation.id}
              type="button"
              onClick={() => controller.selectConversation(conversation.id)}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                 "lemma-assistant-sidebar-conversation-button w-full border px-3 py-3 text-left text-sm transition-colors",
                 assistantRadiusClassName(radius, "item"),
                 isActive
                   ? "border-[color:var(--row-border)] bg-[var(--card-bg)] shadow-[var(--shadow-sm)]"
                   : "lemma-assistant-conversation-list-item-idle",
               )}
            >
              <div className="truncate font-medium">
                {renderConversationLabel({ conversation, isActive })}
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
                <span className={cn("size-1.5 rounded-full flex-shrink-0", conversationStatusDotColor(conversation.status))} />
                <span>{relativeTimeAgo(conversation.updated_at || conversation.created_at)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
}
