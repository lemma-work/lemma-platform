import { useCurrentUser } from "lemma-sdk/react";
import { LemmaMembers } from "@/components/lemma/lemma-members";
import { client } from "@/lib/client";
import { hasPodId, runtimeConfig } from "@/lib/runtime-config";

export function MembersPage() {
  const currentUser = useCurrentUser({ client, enabled: true });
  return (
    <LemmaMembers
      client={client}
      podId={runtimeConfig.podId || undefined}
      enabled={hasPodId}
      currentUserId={currentUser.user?.id ?? null}
      title="Members"
      description="Manage the people who can access and operate this pod."
      allowRoleEdit
      allowRemove
      appearance="contained"
      density="comfortable"
      radius="lg"
    />
  );
}
