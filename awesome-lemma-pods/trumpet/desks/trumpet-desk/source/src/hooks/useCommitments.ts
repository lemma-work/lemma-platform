import { useRecords } from 'lemma-sdk/react';
import { client } from '@/lib/client';
import { runtimeConfig } from '@/lib/runtime-config';
import { TABLES } from '@/lib/resources';
import { dueDateLabel } from '@/lib/time';

export type CommitmentStatus = 'active' | 'completed' | 'missed' | 'snoozed';
export type CommitmentType   = 'to_others' | 'from_others' | 'habit' | 'calendar';

export interface Person {
  id:         string;
  name:       string;
  nickname?:  string;
  photo_url?: string;
  role?:      string;
}

export interface Commitment {
  id:             string;
  title:          string;
  type:           CommitmentType;
  description?:   string;
  person_id?:     string;
  due_date?:      string;
  preferred_time?: string;
  end_time?:      string;
  recurrence?:    string;
  status:         CommitmentStatus;
  external_ref?:  string;
  notes?:         string;
  created_at:     string;
  updated_at:     string;
  // enriched
  dueLabel:       string;
  urgency:        'green' | 'amber' | 'red';
  // joined from people table
  personName?:    string;
  personNickname?: string;
  personPhotoUrl?: string;
}

function enrich(
  raw: Record<string, unknown>,
  peopleMap: Map<string, Person>,
): Commitment {
  const { label, urgency } = dueDateLabel(raw.due_date as string | null);
  const person = raw.person_id ? peopleMap.get(raw.person_id as string) : undefined;
  return {
    ...(raw as unknown as Commitment),
    dueLabel:        label,
    urgency,
    personName:      person?.name,
    personNickname:  person?.nickname,
    personPhotoUrl:  person?.photo_url,
  };
}

/** All active to_others + from_others commitments, enriched with due label and person info. */
export function useCommitments() {
  // Fetch commitments (active only)
  const commitState = useRecords<Record<string, unknown>>({
    client,
    podId:     runtimeConfig.podId,
    tableName: TABLES.commitments,
    filters: [{ field: 'status', operator: 'eq', value: 'active' }],
    sort:    [{ field: 'due_date', direction: 'asc' }],
    limit:   200,
  });

  // Fetch people (no filter — fetch all for join)
  const peopleState = useRecords<Record<string, unknown>>({
    client,
    podId:     runtimeConfig.podId,
    tableName: TABLES.people,
    limit:     100,
  });

  // Build person lookup map
  const peopleMap = new Map<string, Person>();
  for (const p of peopleState.records) {
    peopleMap.set(p.id as string, {
      id:        p.id as string,
      name:      p.name as string,
      nickname:  p.nickname as string | undefined,
      photo_url: p.photo_url as string | undefined,
      role:      p.role as string | undefined,
    });
  }

  const isLoading = commitState.isLoading || peopleState.isLoading;

  const all        = commitState.records.map(r => enrich(r, peopleMap));
  const outbound   = all.filter(c => c.type === 'to_others');
  const inbound    = all.filter(c => c.type === 'from_others');
  const habits     = all.filter(c => c.type === 'habit');

  // Today-or-overdue slice for the Home card
  const todayOutbound = outbound.filter(c => (c.urgency === 'green' && c.dueLabel === 'Today') || c.urgency === 'red');
  const todayInbound  = inbound.filter(c =>  (c.urgency === 'green' && c.dueLabel === 'Today') || c.urgency === 'red');

  return {
    all,
    outbound,
    inbound,
    habits,
    todayOutbound,
    todayInbound,
    isLoading,
    error:   commitState.error,
    refresh: commitState.refresh,
  };
}

/** Mark a commitment completed. Returns updated record or null on error. */
export async function completeCommitment(recordId: string): Promise<Record<string, unknown> | null> {
  try {
    return await client.records.update(TABLES.commitments, recordId, { status: 'completed' });
  } catch {
    return null;
  }
}
