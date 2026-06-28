import { LemmaClient } from "lemma-sdk";
import { runtimeConfig } from "@/lib/runtime-config";

export const client = new LemmaClient({
  ...(runtimeConfig.apiUrl ? { apiUrl: runtimeConfig.apiUrl } : {}),
  ...(runtimeConfig.authUrl ? { authUrl: runtimeConfig.authUrl } : {}),
  ...(runtimeConfig.podId ? { podId: runtimeConfig.podId } : {}),
});
