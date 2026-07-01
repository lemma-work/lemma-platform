export const runtimeConfig = {
  apiUrl: import.meta.env.VITE_LEMMA_API_URL ?? "",
  authUrl: import.meta.env.VITE_LEMMA_AUTH_URL ?? "",
  podId: import.meta.env.VITE_LEMMA_POD_ID ?? "",
  agentName: import.meta.env.VITE_LEMMA_AGENT_NAME ?? "",
};

export const hasPodId = runtimeConfig.podId.trim().length > 0;
