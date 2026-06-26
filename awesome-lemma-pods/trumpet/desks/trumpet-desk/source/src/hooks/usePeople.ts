import { useRecords } from 'lemma-sdk/react';
import { client } from '@/lib/client';
import { runtimeConfig } from '@/lib/runtime-config';
import { TABLES } from '@/lib/resources';

export interface PersonRecord {
  id:          string;
  name:        string;
  nickname?:   string;
  photo_url?:  string;
  role?:       string;
  email?:      string;
  agentNotes?: string;
}

// notes column stores JSON: { agentNotes?: string }
function parseAgentNotes(raw: unknown): string {
  if (!raw || typeof raw !== 'string') return '';
  try { return (JSON.parse(raw) as Record<string, unknown>).agentNotes as string ?? ''; }
  catch { return raw; }
}

export function usePeople() {
  const state = useRecords<Record<string, unknown>>({
    client,
    podId:     runtimeConfig.podId,
    tableName: TABLES.people,
    limit:     200,
  });

  const people: PersonRecord[] = state.records.map(r => ({
    id:         r.id        as string,
    name:       r.name      as string,
    nickname:   r.nickname  as string | undefined,
    photo_url:  r.photo_url as string | undefined,
    role:       r.role      as string | undefined,
    email:      r.email     as string | undefined,
    agentNotes: parseAgentNotes(r.notes),
  }));

  const peopleContext = buildPeopleContext(people);

  return {
    people,
    peopleContext,
    isLoading: state.isLoading,
    error:     state.error,
    refresh:   state.refresh,
  };
}

export function buildPeopleContext(people: PersonRecord[]): string {
  if (people.length === 0) return '';
  const lines = people.map(p => {
    let line = p.name;
    if (p.email)      line += ` (${p.email})`;
    if (p.role)       line += ` — ${p.role}`;
    if (p.agentNotes) line += `. Note: ${p.agentNotes}`;
    if (!p.email)     line += ' ⚠️ No email on file.';
    return line;
  });
  return [
    '## Your People',
    '',
    ...lines,
    '',
    'RULE: Only use the email addresses listed above when sending messages or emails.',
    'If a person is not listed, or has no email on file, ask the user to add their contact in the People tab before proceeding.',
  ].join('\n');
}
