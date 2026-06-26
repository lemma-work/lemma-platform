import { useAssistantController as useAgentController } from "lemma-sdk/react";
import { AssistantExperienceView as AgentExperienceView } from "@/components/lemma/assistant/assistant-experience";
import { appConfig } from "@/app-config";
import { client } from "@/lib/client";
import { hasPodId, runtimeConfig } from "@/lib/runtime-config";

function useAgentChatController() {
  return useAgentController({
    client,
    podId: runtimeConfig.podId || undefined,
    agentName: runtimeConfig.agentName || appConfig.agent?.agentName || undefined,
    enabled: hasPodId && Boolean(runtimeConfig.agentName || appConfig.agent?.agentName),
  });
}

export function AgentChatPage() {
  const controller = useAgentChatController();
  return (
    <section className="flex h-full min-h-[34rem] overflow-hidden">
      <AgentExperienceView
        controller={controller}
        title="Mr Toot"
        mode="page"
        showConversationList
        className="h-full min-h-0 flex-1"
        appearance="default"
        density="comfortable"
        radius="lg"
        chromeStyle="subtle"
      />
    </section>
  );
}

export function AgentChatRail() {
  const controller = useAgentChatController();
  return (
    <aside className="hidden min-h-0 border-l border-border bg-background/80 xl:flex">
      <AgentExperienceView
        controller={controller}
        title="Mr Toot"
        mode="side-panel"
        showConversationList={false}
        className="h-full min-h-0 flex-1"
        appearance="default"
        density="compact"
        radius="lg"
        chromeStyle="subtle"
      />
    </aside>
  );
}

export function AgentChatPopup() {
  const controller = useAgentChatController();
  return (
    <AgentExperienceView
      controller={controller}
      title="Mr Toot"
      mode="popup"
      popupTriggerLabel="Mr Toot"
      popupTriggerVariant="pill"
      showConversationList={false}
      appearance="default"
      density="comfortable"
      radius="lg"
      chromeStyle="subtle"
    />
  );
}
