'use client';

import { useMemo } from 'react';
import Image from 'next/image';
import Link from 'next/link';

import { useAgents } from '@/lib/hooks/use-agents';
import { usePodMembers } from '@/lib/hooks/use-pod-members';
import { usePodSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { useSchedules } from '@/lib/hooks/use-schedules';
import { TONE_COUNT as IDENTITY_TONE_COUNT } from '@/lib/identity/seeded-identity';
import { getSurfaceDefinition } from '@/lib/surfaces/registry';
import { getSurfacePlatformKey } from '@/lib/utils/surfaces';
import { getScheduleTargetName } from '@/lib/utils/schedules';
import { isConversationRunningStatus } from '@/lib/utils/conversations';
import { formatAgentName } from '@/lib/utils/agents';
import type { Conversation } from '@/lib/types';

const MAX_FACES = 5;

function initialOf(label: string): string {
    const trimmed = label.trim();
    return trimmed ? trimmed[0].toUpperCase() : '?';
}

/**
 * Deterministic per-person tint, so the same face keeps the same colour — drawn
 * from the identity system's tone pool, the palette built for "a distinct
 * being" and already worn by the agent faces standing beside these avatars.
 *
 * It used to draw from four fixed tints of its own, three of which were state
 * colours: `--accent-rgb`, `--state-success` and `--state-warning`. The comment
 * defending that pool said it existed so a hash would not "drift into the state
 * colours and start implying an agent is failing" — and then picked them
 * anyway, which put two people in success-green one scroll above an Activity
 * panel that uses success-green for "Completed". A person is not a status, and
 * a hue cannot mean both.
 */
function avatarToneClass(seed: string): string {
    let hash = 0;
    for (let index = 0; index < seed.length; index += 1) {
        hash = (hash * 31 + seed.charCodeAt(index)) % 997;
    }
    // `hue`, not `tone`: the tone variant also sets `color`, and
    // `resource-identity.css` imports after this feature sheet, so it would win
    // over the avatar's white initial and paint the letter the same colour as
    // the disc behind it. The hue variant carries the custom properties only —
    // which is the split that file documents, for exactly this case.
    return `lm-identity-hue-${hash % IDENTITY_TONE_COUNT}`;
}

interface PresenceFace {
    key: string;
    label: string;
    kind: 'person' | 'agent';
    iconUrl?: string | null;
}

/**
 * The room.
 *
 * People and agents in one row of faces, because a pod is the boundary they
 * share — the humans and the agents inside it are the same kind of fact about
 * this pod, not a roster and a footnote. Agents are drawn square against the
 * people's circles so the mix is readable at a glance.
 *
 * Every list here is already in cache: members, agents, surfaces and schedules
 * are loaded by the pod shell and by home's own activity region.
 */
export function PodHomePresence({
    podId,
    conversations,
}: {
    podId: string;
    conversations: Conversation[];
}) {
    const podAccess = usePodAccess(podId);
    const canReadAgents = podAccess.can('agent.read');
    const canReadSchedules = podAccess.can('schedule.read');
    const canReadSurfaces = podAccess.canAccessRoute('surfaces');
    const { data: membersData } = usePodMembers(podId);
    const { data: agentsData } = useAgents(canReadAgents ? podId : undefined);
    const { data: surfaces = [] } = usePodSurfaces(canReadSurfaces ? podId : undefined);
    const { data: schedulesData } = useSchedules(canReadSchedules ? podId : undefined, { isActive: true, limit: 12 });

    const members = useMemo(() => membersData?.items ?? [], [membersData?.items]);
    const agents = useMemo(() => agentsData?.items ?? [], [agentsData?.items]);

    // "On duty" is a promise about the future, so it is read off active
    // schedules rather than off the agent list — an agent nobody has scheduled
    // is available, not on duty.
    const onDuty = useMemo(() => {
        const byName = new Map(agents.map((agent) => [agent.name, agent]));
        const seen = new Set<string>();
        const result: Array<{ name: string; iconUrl?: string | null }> = [];

        for (const schedule of schedulesData?.items || []) {
            if (schedule.is_active === false) continue;
            const target = getScheduleTargetName(schedule);
            if (!target || seen.has(target)) continue;
            seen.add(target);
            result.push({ name: formatAgentName(target), iconUrl: byName.get(target)?.icon_url });
        }

        return result;
    }, [agents, schedulesData?.items]);

    const runningCount = useMemo(
        () => conversations.filter((conversation) => isConversationRunningStatus(conversation.status)).length,
        [conversations],
    );

    const faces = useMemo<PresenceFace[]>(() => {
        const people: PresenceFace[] = members.map((member) => ({
            key: `person-${member.user_id}`,
            label: member.user_name || member.user_email || member.user_id,
            kind: 'person',
        }));
        const working: PresenceFace[] = onDuty.map((agent) => ({
            key: `agent-${agent.name}`,
            label: agent.name,
            kind: 'agent',
            iconUrl: agent.iconUrl,
        }));

        return [...people, ...working].slice(0, MAX_FACES);
    }, [members, onDuty]);

    const surfacePlatforms = useMemo(() => {
        const seen = new Map<string, { key: string; label: string; logoSrc?: string }>();
        for (const surface of surfaces) {
            if (String(surface.status || '').toUpperCase() !== 'ACTIVE') continue;
            const key = getSurfacePlatformKey(surface);
            if (seen.has(key)) continue;
            const definition = getSurfaceDefinition(key);
            seen.set(key, { key, label: definition?.label || key, logoSrc: definition?.logoSrc });
        }
        return [...seen.values()];
    }, [surfaces]);

    const peopleLabel = members.length === 1 ? '1 person' : `${members.length} people`;
    const hasPeople = members.length > 0;
    const hasDuty = onDuty.length > 0;
    const hasSurfaces = surfacePlatforms.length > 0;

    if (!hasPeople && !hasDuty && !hasSurfaces) return null;

    return (
        <div className="pod-home-presence">
            {faces.length > 0 ? (
                <span className="pod-home-presence-faces" aria-hidden="true">
                    {faces.map((face) => (
                        <span
                            key={face.key}
                            className={`pod-home-presence-avatar pod-home-presence-avatar-${face.kind} ${avatarToneClass(face.label)}`}
                            title={face.label}
                        >
                            {face.iconUrl ? (
                                <Image src={face.iconUrl} alt="" width={16} height={16} className="object-contain" />
                            ) : (
                                initialOf(face.label)
                            )}
                        </span>
                    ))}
                </span>
            ) : null}

            <span className="pod-home-presence-copy">
                {hasPeople ? (
                    <Link href={`/pod/${podId}/settings/members`} className="pod-home-presence-link custom-focus-ring">
                        {peopleLabel}
                    </Link>
                ) : null}

                {hasPeople && hasDuty ? <span className="pod-home-presence-sep" aria-hidden="true" /> : null}

                {hasDuty ? (
                    <span className="pod-home-presence-duty">
                        {runningCount > 0 ? <span className="pod-home-presence-live lemma-live-pulse" aria-hidden="true" /> : null}
                        <b>{onDuty[0].name}</b>
                        {onDuty.length > 1 ? ` and ${onDuty.length - 1} more` : ''}
                        {runningCount > 0 ? ' working now' : ' on duty'}
                    </span>
                ) : null}

                {hasDuty && hasSurfaces ? <span className="pod-home-presence-sep" aria-hidden="true" /> : null}

                {hasSurfaces ? (
                    // The agents index is `/ai`; `/agents` only holds `[agentId]`
                    // and `new`, so a bare link there is a 404.
                    /* The marks carry their own colour, so they take no
                       `surface-logo-chip` plate. That plate is for a monochrome
                       mark that would vanish on dark stock; on these it only
                       pasted three near-white tiles into a line of prose, which
                       is the one thing on the row that did not belong to either
                       appearance. */
                    <Link href={`/pod/${podId}/ai`} className="pod-home-presence-link custom-focus-ring">
                        <span className="pod-home-presence-surfaces">
                            {surfacePlatforms.map((platform) => (
                                <span key={platform.key} className="pod-home-presence-surface" title={platform.label}>
                                    {platform.logoSrc ? (
                                        <Image src={platform.logoSrc} alt="" width={15} height={15} className="object-contain" aria-hidden="true" />
                                    ) : (
                                        initialOf(platform.label)
                                    )}
                                </span>
                            ))}
                        </span>
                        reachable in {surfacePlatforms.map((platform) => platform.label).join(', ')}
                    </Link>
                ) : null}
            </span>
        </div>
    );
}
