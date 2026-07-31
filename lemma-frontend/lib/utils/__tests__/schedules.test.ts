import { describe, expect, it } from 'vitest';

import {
    buildCronExpression,
    describeCron,
    describeScheduleConfig,
    getScheduleConfigDetails,
    getScheduleDatastoreConfig,
    getScheduleTargetKind,
    getScheduleTargetName,
    getScheduleTimeConfig,
    getScheduleWebhookConfig,
    parseCronExpression,
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
