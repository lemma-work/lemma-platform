import { describe, expect, it } from 'vitest';

import type { Notification } from '@/lib/hooks/use-notifications';
import {
    canDismissNotification,
    getNotificationActionHref,
    getNotificationFormAction,
    getNotificationStateLabel,
    getNotificationTone,
    groupIdenticalAsks,
    isFromToday,
    isUndelivered,
    sharedUndeliverableReason,
    shortRelativeTime,
} from './notification-display';

/**
 * The rules three surfaces share, tested where they are decided.
 *
 * All of it is pure by design — the bell, home and the inbox agree about what a
 * row says only because none of them decides it. That is exactly the kind of
 * thing that is cheap to pin down and expensive to leave to a browser.
 */

let seq = 0;

function notification(overrides: Partial<Notification> = {}): Notification {
    seq += 1;
    return {
        id: `n${seq}`,
        pod_id: 'pod',
        title: 'Weekly check-in',
        body: 'Did the invoice go out?',
        origin_kind: 'AGENT_RUN',
        actor_agent_id: 'agent-1',
        status: 'OPEN',
        delivery_status: 'DELIVERED',
        expects_response: true,
        awaiting_response: true,
        responds_through_action: false,
        created_at: '2026-08-16T09:00:00Z',
        ...overrides,
    };
}

describe('groupIdenticalAsks', () => {
    it('collapses a schedule that asked the same thing six times', () => {
        const items = Array.from({ length: 6 }, () => notification());
        const groups = groupIdenticalAsks(items);

        expect(groups).toHaveLength(1);
        expect(groups[0].items).toHaveLength(6);
        // The list arrives newest first, so the first member is the one worth
        // answering and the one whose timestamp the card prints.
        expect(groups[0].latest).toBe(items[0]);
    });

    it('keys on content alone, so a group survives losing its newest member', () => {
        const items = [notification(), notification()];
        const [before] = groupIdenticalAsks(items);
        // Answering settles the newest first. If the key carried that id, the
        // card would remount here and drop a half-typed answer.
        const [after] = groupIdenticalAsks(items.slice(1));

        expect(after.key).toBe(before.key);
    });

    it('keeps a form ask and a text ask apart', () => {
        const groups = groupIdenticalAsks([
            notification({ responds_through_action: true }),
            notification({ responds_through_action: false }),
        ]);

        expect(groups).toHaveLength(2);
    });

    it('never collapses two forms, however alike they read', () => {
        // Each is a distinct run suspended on a distinct wait. One card's form
        // resumes exactly one of them, so "asked 2 times" would promise to
        // settle work it cannot reach.
        const groups = groupIdenticalAsks([
            notification({ responds_through_action: true }),
            notification({ responds_through_action: true }),
        ]);

        expect(groups).toHaveLength(2);
        expect(groups.every((group) => group.items.length === 1)).toBe(true);
    });

    it('keeps a question and a notice apart', () => {
        // One is answered and one is dismissed. Collapsed together, the card
        // offers whichever the newest wants and the API refuses it for the rest.
        const groups = groupIdenticalAsks([
            notification({ expects_response: true }),
            notification({ expects_response: false, awaiting_response: false }),
        ]);

        expect(groups).toHaveLength(2);
    });

    it('keeps two agents asking the same question apart', () => {
        const groups = groupIdenticalAsks([
            notification({ actor_agent_id: 'agent-1' }),
            notification({ actor_agent_id: 'agent-2' }),
        ]);

        expect(groups).toHaveLength(2);
    });

    it('preserves first-appearance order across groups', () => {
        const first = notification({ title: 'Newest' });
        const second = notification({ title: 'Older' });
        const repeat = notification({ title: 'Newest' });

        expect(groupIdenticalAsks([first, second, repeat]).map((g) => g.latest.title)).toEqual([
            'Newest',
            'Older',
        ]);
    });
});

describe('sharedUndeliverableReason', () => {
    const undeliverable = (reason: string | null) =>
        notification({ delivery_status: 'UNDELIVERABLE', undeliverable_reason: reason });

    it('hoists a reason every open ask shares', () => {
        const reason = 'The pod has no active surface to reach anyone on.';
        expect(sharedUndeliverableReason([undeliverable(reason), undeliverable(reason)])).toBe(
            reason,
        );
    });

    it('stays silent when one of them did reach somebody', () => {
        // One delivered row makes "nothing could be delivered" false, and a
        // banner that overstates is worse than a note per card.
        expect(
            sharedUndeliverableReason([undeliverable('No surface.'), notification()]),
        ).toBeNull();
    });

    it('stays silent when the reasons disagree', () => {
        expect(
            sharedUndeliverableReason([undeliverable('No surface.'), undeliverable('No email.')]),
        ).toBeNull();
    });

    it('stays silent when there is no reason to hoist', () => {
        // Nothing to put in the banner — the cards say it themselves instead.
        expect(sharedUndeliverableReason([undeliverable(null)])).toBeNull();
    });

    it('counts a failed send as undelivered', () => {
        const reason = 'The chat surface rejected it.';
        expect(
            sharedUndeliverableReason([
                notification({ delivery_status: 'FAILED', undeliverable_reason: reason }),
                notification({ delivery_status: 'UNDELIVERABLE', undeliverable_reason: reason }),
            ]),
        ).toBe(reason);
    });

    it('has nothing to say about an empty list', () => {
        expect(sharedUndeliverableReason([])).toBeNull();
    });
});

describe('isUndelivered', () => {
    it('covers both halves of the failure', () => {
        expect(isUndelivered(notification({ delivery_status: 'UNDELIVERABLE' }))).toBe(true);
        expect(isUndelivered(notification({ delivery_status: 'FAILED' }))).toBe(true);
        expect(isUndelivered(notification({ delivery_status: 'DELIVERED' }))).toBe(false);
        expect(isUndelivered(notification({ delivery_status: 'PENDING' }))).toBe(false);
    });
});

describe('getNotificationFormAction', () => {
    const action = {
        type: 'WORKFLOW_FORM',
        run_id: 'run-1',
        flow_id: 'flow-1',
        node_id: 'collect',
        schema: { type: 'object', properties: { amount: { type: 'number' } } },
    };

    it('reads the resolved schema the executor put on the payload', () => {
        expect(
            getNotificationFormAction(
                notification({ responds_through_action: true, action }),
            ),
        ).toEqual({ runId: 'run-1', nodeId: 'collect', schema: action.schema });
    });

    it('is nothing for an ask answered in prose', () => {
        expect(
            getNotificationFormAction(notification({ responds_through_action: false, action })),
        ).toBeNull();
    });

    it('refuses a payload the submit endpoint could not match', () => {
        // `node_id` is what the run checks against its active wait; without it
        // there is no form to draw, only one to link to.
        const withoutNode = { ...action, node_id: undefined };
        expect(
            getNotificationFormAction(
                notification({ responds_through_action: true, action: withoutNode }),
            ),
        ).toBeNull();
        expect(
            getNotificationFormAction(notification({ responds_through_action: true })),
        ).toBeNull();
    });
});

describe('getNotificationActionHref', () => {
    const formNotification = notification({
        responds_through_action: true,
        action: { run_id: 'run-1', flow_id: 'flow-1', node_id: 'collect' },
    });

    it('builds the run route off the workflow name, not its id', () => {
        expect(getNotificationActionHref('pod-1', formNotification, () => 'invoices')).toBe(
            '/pod/pod-1/flows/invoices/runs/run-1',
        );
    });

    it('is nothing when the name cannot be resolved', () => {
        // Which is also the honest answer for somebody who cannot read
        // workflows: there is no page there for them.
        expect(getNotificationActionHref('pod-1', formNotification, () => undefined)).toBeNull();
    });
});

describe('canDismissNotification', () => {
    it('allows it only where the domain does', () => {
        // `acknowledge` is refused for anything expecting a response, so a
        // Dismiss button there is one that can only ever error.
        expect(canDismissNotification(notification({ expects_response: false }))).toBe(true);
        expect(canDismissNotification(notification({ expects_response: true }))).toBe(false);
        expect(
            canDismissNotification(notification({ status: 'RESPONDED', expects_response: false })),
        ).toBe(false);
    });
});

describe('getNotificationStateLabel', () => {
    it('says nothing for a plain open notice', () => {
        // The whole list is open, so stamping every row with it costs a column
        // and says nothing.
        expect(
            getNotificationStateLabel(
                notification({ awaiting_response: false, expects_response: false }),
            ),
        ).toBeNull();
    });

    it('names the states that have left OPEN', () => {
        expect(getNotificationStateLabel(notification({ status: 'RESPONDED' }))).toBe('Answered');
        expect(getNotificationStateLabel(notification({ status: 'EXPIRED' }))).toBe('Expired');
        expect(getNotificationStateLabel(notification())).toBe('Needs a reply');
    });
});

describe('getNotificationTone', () => {
    it('is about what is owed, not how the message travelled', () => {
        // An undeliverable notification is sitting right here; colouring it as
        // an error would say the opposite.
        expect(getNotificationTone(notification({ delivery_status: 'UNDELIVERABLE' }))).toBe(
            'attention',
        );
        expect(getNotificationTone(notification({ status: 'RESPONDED' }))).toBe('success');
        expect(getNotificationTone(notification({ status: 'CANCELLED' }))).toBe('danger');
        expect(getNotificationTone(notification({ awaiting_response: false }))).toBe('muted');
    });
});

describe('shortRelativeTime', () => {
    it('drops the "ago" a whole column would otherwise repeat', () => {
        const hoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000).toISOString();
        expect(shortRelativeTime(hoursAgo)).toBe('3h');
        expect(shortRelativeTime(new Date().toISOString())).toBe('now');
        expect(shortRelativeTime(null)).toBeNull();
    });
});

describe('isFromToday', () => {
    it('bands on the calendar day, and refuses to guess', () => {
        expect(isFromToday(new Date().toISOString())).toBe(true);
        expect(isFromToday(new Date(Date.now() - 40 * 60 * 60 * 1000).toISOString())).toBe(false);
        expect(isFromToday('not a date')).toBe(false);
        expect(isFromToday(null)).toBe(false);
    });
});
