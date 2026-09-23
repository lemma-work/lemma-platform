"use client";

import { useSearchParams } from "next/navigation";

import { ProtectedRoute } from "@/components/auth/protected-route";
import { CreatePodScreen } from "@/components/pod/create-pod-screen";

export default function CreatePodPage() {
  return (
    <ProtectedRoute>
      <CreatePodRoute />
    </ProtectedRoute>
  );
}

function CreatePodRoute() {
  const searchParams = useSearchParams();

  return <CreatePodScreen remixSource={searchParams.get("remixSource")} />;
}
