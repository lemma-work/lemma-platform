import { describe, expect, it } from 'vitest';

import {
    availableConditionOperators,
    buildCronExpression,
    buildMatchConditions,
    describeCron,
    describeScheduleConfig,
    getScheduleConfigDetails,
    getScheduleDatastoreConfig,
    getScheduleTargetKind,
    getScheduleTargetName,
    getScheduleTimeConfig,
    getScheduleWebhookConfig,
    parseCronExpression,
    parseMatchConditions,
    type TimeCadence,
} from '../schedules';

describe('schedule formatting helpers', () => {
    it('describes common cron expressions', () => {
        expect(describeCron('0 * * * *')).toBe('Every hour');
        expect(describeCron('0 */6 * * *')).toBe('Every 6 hours');
        expect(describeCron('30 9 * * 1-5')).toBe('Weekdays at 09:30');
        expect(describeCron('15 8 1 * *')).toBe('1st of each month at 08:15');
    });

    it('extracts target kind and name from workflow and agent schedules', () => {
        expect(getScheduleTargetKind({ workflow_name: 'daily-review' } as never)).toBe('workflow');
        expect(getScheduleTargetName({ workflow_name: 'daily-review' } as never)).toBe('daily-review');

        expect(getScheduleTargetKind({ agent_name: 'triage-agent' } as never)).toBe('agent');
        expect(getScheduleTargetName({ agent_name: 'triage-agent' } as never)).toBe('triage-agent');
    });

    it('flattens nested schedule config details', () => {
        const schedule = {
            schedule_type: 'TIME',
            config: JSON.stringify({
                schedule: {
                    cron_expression: '0 10 * * *',
                    timezone: 'Asia/Kolkata',
                },
            }),
        } as never;

        expect(describeScheduleConfig(schedule)).toBe('Daily at 10:00 · Asia/Kolkata');
        expect(getScheduleConfigDetails(schedule)).toEqual([
            { label: 'Cron', value: '0 10 * * *' },
            { label: 'TZ', value: 'Asia/Kolkata' },
        ]);
    });
});

describe('parseCronExpression', () => {
    // Editing a trigger only works if the cadence controls can be rehydrated
    // from what was saved — so every cadence the controls can build has to come
    // back as that same cadence.
    const cases: Array<{
        cadence: TimeCadence;
        timeOfDay: string;
        weeklyDays: string[];
        monthDay: number;
    }> = [
        { cadence: 'hourly', timeOfDay: '09:00', weeklyDays: ['1'], monthDay: 1 },
        { cadence: 'daily', timeOfDay: '07:45', weeklyDays: ['1'], monthDay: 1 },
        { cadence: 'weekdays', timeOfDay: '09:30', weeklyDays: ['1'], monthDay: 1 },
        { cadence: 'weekly', timeOfDay: '18:05', weeklyDays: ['2', '4'], monthDay: 1 },
        { cadence: 'monthly', timeOfDay: '00:15', weeklyDays: ['1'], monthDay: 12 },
    ];

    it.each(cases)('round-trips $cadence through build and parse', (input) => {
        const cron = buildCronExpression({ ...input, customCron: '' });
        const parsed = parseCronExpression(cron);

        expect(parsed.cadence).toBe(input.cadence);
        if (input.cadence !== 'hourly') expect(parsed.timeOfDay).toBe(input.timeOfDay);
        if (input.cadence === 'weekly') expect(parsed.weeklyDays).toEqual(input.weeklyDays);
        if (input.cadence === 'monthly') expect(parsed.monthDay).toBe(input.monthDay);
    });

    it('falls back to custom, losing nothing, for expressions the controls cannot say', () => {
        expect(parseCronExpression('*/5 * * * *')).toMatchObject({
            cadence: 'custom',
            customCron: '*/5 * * * *',
        });
        expect(parseCronExpression('0 9 1 3 *')).toMatchObject({ cadence: 'custom' });
        expect(parseCronExpression('not a cron')).toMatchObject({ cadence: 'custom' });
    });
});

describe('typed schedule config readers', () => {
    it('reads a time trigger, defaulting the timezone rather than leaving it blank', () => {
        expect(getScheduleTimeConfig({
            schedule_type: 'TIME',
            config: { cron_expression: '0 9 * * 1-5', timezone: 'Europe/London' },
        } as never)).toEqual({ cron: '0 9 * * 1-5', timezone: 'Europe/London' });

        expect(getScheduleTimeConfig({
            schedule_type: 'TIME',
            config: { cron_expression: '0 9 * * *' },
        } as never)).toEqual({ cron: '0 9 * * *', timezone: 'UTC' });
    });

    it('reads a data trigger and drops operations it does not recognise', () => {
        expect(getScheduleDatastoreConfig({
            schedule_type: 'DATASTORE',
            config: { table_name: 'leads', operations: ['insert', 'DELETE', 'BOGUS'] },
        } as never)).toEqual({ tableName: 'leads', operations: ['INSERT', 'DELETE'] });
    });

    it('reads a webhook trigger from either the column or the config', () => {
        expect(getScheduleWebhookConfig({
            schedule_type: 'WEBHOOK',
            connector_trigger_id: 'GMAIL_NEW_EMAIL',
            config: { connector_id: 'gmail' },
        } as never)).toEqual({ connectorId: 'gmail', triggerId: 'GMAIL_NEW_EMAIL' });

        expect(getScheduleWebhookConfig({
            schedule_type: 'WEBHOOK',
            config: { connector_id: 'slack', connector_trigger_id: 'SLACK_NEW_MESSAGE' },
        } as never)).toEqual({ connectorId: 'slack', triggerId: 'SLACK_NEW_MESSAGE' });
    });
});

describe('match conditions on a data trigger', () => {
    const scheduleWith = (when: unknown) =>
        ({ schedule_type: 'DATASTORE', config: { table_name: 'tickets', operations: ['UPDATE'], when } }) as never;

    it('builds the wire shape from builder rows', () => {
        expect(
            buildMatchConditions([
                { column: 'status', operator: 'became', value: 'approved' },
                { column: 'priority', operator: 'is', value: 'high' },
            ]),
        ).toEqual({ status: { to: 'approved' }, priority: { equals: 'high' } });
    });

    it('coerces a value to the type the stored row actually holds', () => {
        // A number column holds 5, never "5" — sending the string would build a
        // condition that silently never matches.
        expect(
            buildMatchConditions([{ column: 'count', operator: 'is', value: '5' }], { count: 'INTEGER' }),
        ).toEqual({ count: { equals: 5 } });
        expect(
            buildMatchConditions([{ column: 'done', operator: 'is', value: 'true' }], { done: 'BOOLEAN' }),
        ).toEqual({ done: { equals: true } });
        expect(
            buildMatchConditions([{ column: 'name', operator: 'is', value: '5' }], { name: 'TEXT' }),
        ).toEqual({ name: { equals: '5' } });
    });

    it('leaves an unparseable number alone rather than sending NaN', () => {
        expect(
            buildMatchConditions([{ column: 'count', operator: 'is', value: 'lots' }], { count: 'INTEGER' }),
        ).toEqual({ count: { equals: 'lots' } });
    });

    it('carries no value for a test that is about the change itself', () => {
        expect(buildMatchConditions([{ column: 'owner', operator: 'changed', value: '' }])).toEqual({
            owner: { changed: true },
        });
    });

    it('is undefined when there is nothing to say', () => {
        expect(buildMatchConditions([])).toBeUndefined();
        // A half-finished row is dropped, not sent as an empty object.
        expect(buildMatchConditions([{ column: 'status', operator: 'is', value: '  ' }])).toBeUndefined();
    });

    it('merges several tests on one column', () => {
        expect(
            buildMatchConditions([
                { column: 'status', operator: 'was', value: 'pending' },
                { column: 'status', operator: 'became', value: 'approved' },
            ]),
        ).toEqual({ status: { from: 'pending', to: 'approved' } });
    });

    it('round-trips through the wire shape', () => {
        const rows = [
            { column: 'status', operator: 'became' as const, value: 'approved' },
            { column: 'owner', operator: 'changed' as const, value: '' },
        ];
        const parsed = parseMatchConditions(scheduleWith(buildMatchConditions(rows)));
        expect(parsed.conditions).toEqual(rows);
        expect(parsed.unsupported).toEqual([]);
    });

    it('names a test it cannot draw instead of dropping it', () => {
        // `in` and `written` are reachable from a bundle or the CLI; editing a
        // trigger in the modal must not silently discard them.
        const parsed = parseMatchConditions(scheduleWith({ status: { in: ['a', 'b'] }, notes: { written: true } }));
        expect(parsed.conditions).toEqual([]);
        expect(parsed.unsupported).toEqual(['status in', 'notes written']);
    });

    it('treats changed:false as undrawable rather than as changed:true', () => {
        const parsed = parseMatchConditions(scheduleWith({ status: { changed: false } }));
        expect(parsed.conditions).toEqual([]);
        expect(parsed.unsupported).toEqual(['status changed']);
    });

    it('offers only the operators the chosen change types can answer', () => {
        // The API rejects a condition no operation could satisfy, so a trigger
        // that can never fire should be unreachable in the builder.
        expect(availableConditionOperators(['INSERT'])).toEqual(['is', 'is_not', 'became']);
        expect(availableConditionOperators(['DELETE'])).toEqual(['is', 'is_not']);
        expect(availableConditionOperators(['UPDATE'])).toEqual(['is', 'is_not', 'became', 'was', 'changed']);
    });

    it('has no conditions when the trigger carries none', () => {
        expect(parseMatchConditions(scheduleWith(undefined)).conditions).toEqual([]);
    });
});
