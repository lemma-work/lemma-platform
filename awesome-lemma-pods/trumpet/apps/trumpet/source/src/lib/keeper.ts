import { client } from '@/lib/client';
import { AGENTS } from '@/lib/resources';
import { parseAssistantStreamEvent } from 'lemma-sdk';

export async function createKeeperConversation(): Promise<{ id: string }> {
  return client.conversations.createForAgent(AGENTS.mrToot);
}

export async function streamKeeperMessage(
  conversationId: string,
  message: string,
  signal?: AbortSignal,
): Promise<string> {
  const stream = await client.conversations.sendMessageStream(
    conversationId,
    { content: message },
    { signal },
  );

  const reader  = stream.getReader();
  const decoder = new TextDecoder();
  let buffer    = '';
  let text      = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.startsWith('data:')) continue;
        const raw = line.slice(5).trim();
        if (raw === '[DONE]' || !raw) continue;
        try {
          const evt = parseAssistantStreamEvent(JSON.parse(raw));
          if (evt?.type === 'text_delta' && evt.delta) text += evt.delta;
        } catch { /* skip malformed SSE frame */ }
      }
    }
  } finally {
    reader.releaseLock();
  }
  return text.trim();
}
