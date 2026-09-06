"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { PlainPageShell } from "@/components/dashboard/plain-page-shell";
import { UsageOverview } from "@/components/usage/usage-overview";
import { ProtectedRoute } from "@/components/auth/protected-route";

function MyUsage() {
  const params = useSearchParams();
  const organizationId = params.get("organizationId") || undefined;
  return (
    <PlainPageShell
      title="My usage"
      backHref="/profile"
      backLabel="Profile"
      contentWidthClassName="max-w-6xl"
    >
      <UsageOverview
        key={organizationId ?? "personal"}
        organizationId={organizationId}
        scope="personal"
      />
    </PlainPageShell>
  );
}

export default function MyUsagePage() {
  return (
    <ProtectedRoute>
      <Suspense>
        <MyUsage />
      </Suspense>
    </ProtectedRoute>
  );
}
