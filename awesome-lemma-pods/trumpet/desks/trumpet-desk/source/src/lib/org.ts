import { client } from '@/lib/client';
import { runtimeConfig } from '@/lib/runtime-config';

let cachedOrgId: string | null = null;

export async function getOrgId(): Promise<string> {
  if (cachedOrgId) return cachedOrgId;

  const podId = runtimeConfig.podId;
  if (!podId) throw new Error('No pod ID configured — set VITE_LEMMA_POD_ID');

  const pod = await client.pods.get(podId);
  const orgId = (pod as Record<string, unknown>).organization_id as string;
  if (!orgId) throw new Error('Could not resolve org ID from pod');

  cachedOrgId = orgId;
  return orgId;
}
