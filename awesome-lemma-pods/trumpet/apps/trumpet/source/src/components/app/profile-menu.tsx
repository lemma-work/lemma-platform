import * as React from "react";
import { useAuth, useCurrentUser } from "lemma-sdk/react";
import { LemmaUserMenu } from "@/components/lemma/lemma-user-menu";
import { client } from "@/lib/client";

export function ProfileMenu({ sidebar = false }: { sidebar?: boolean }) {
  const auth = useAuth(client);
  const currentUser = useCurrentUser({ client, enabled: auth.isAuthenticated });
  const [busy, setBusy] = React.useState(false);
  const name = [currentUser.user?.first_name, currentUser.user?.last_name].filter(Boolean).join(" ") || currentUser.user?.email || auth.user?.email || "Account";
  const email = currentUser.user?.email || auth.user?.email || undefined;

  async function signOut() {
    if (busy) return;
    setBusy(true);
    try {
      await client.auth.signOut();
      window.location.reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <LemmaUserMenu
      userName={name}
      userEmail={email}
      isOnline={auth.isAuthenticated}
      menuItems={[]}
      onSignOut={() => void signOut()}
      appearance="contained"
      density="comfortable"
      radius="lg"
      className={sidebar ? "w-full justify-between" : undefined}
    />
  );
}
