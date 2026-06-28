import { client } from './client';

// Blob URL cache — persists for the session, prevents re-fetching the same pod file
const cache = new Map<string, string>();

/**
 * Resolves a photo_url to a usable <img src> value.
 * - http/https or /photos/ → returned as-is (static or external)
 * - /pod/… → downloaded via the SDK (auth cookies), cached as an object URL
 */
export async function resolveFileUrl(path: string): Promise<string> {
  if (!path.startsWith('/pod/')) return path;
  const hit = cache.get(path);
  if (hit) return hit;
  const blob = await client.files.download(path);
  const url = URL.createObjectURL(blob);
  cache.set(path, url);
  return url;
}

export function isPodPath(path: string | undefined): boolean {
  return !!path?.startsWith('/pod/');
}
