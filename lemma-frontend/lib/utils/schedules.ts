import { DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';
import type { Schedule } from '@/lib/types';

export type TimeCadence = 'hourly' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'custom';

function splitTime(value: string): { hour: string; minute: string } {
    const [hour = '09', minute = '00'] = value.split(':');
    return {
        hour: hour.padStart(2, '0'),
        minute: minute.padStart(2, '0'),
    };
}

/** Build a 5-field cron expression from the compact cadence controls. */
export function buildCronExpression({
    cadence,
    timeOfDay,
    weeklyDays,
    monthDay,
    customCron,
}: {
    cadence: TimeCadence;
    timeOfDay: string;
    weeklyDays: string[];
    monthDay: number;
    customCron: string;
}): string {
    if (cadence === 'custom') return customCron.trim();
    if (cadence === 'hourly') return '0 * * * *';

    const { hour, minute } = splitTime(timeOfDay);
    if (cadence === 'daily') return `${minute} ${hour} * * *`;
    if (cadence === 'weekdays') return `${minute} ${hour} * * 1-5`;
    if (cadence === 'weekly') return `${minute} ${hour} * * ${weeklyDays.length ? weeklyDays.join(',') : '1'}`;
    return `${minute} ${hour} ${Math.min(31, Math.max(1, monthDay))} * *`;
}

/**
 * The inverse of `buildCronExpression` — rehydrate the compact cadence controls
 * from a stored expression so editing a trigger starts from what it actually
 * does, not from the defaults. Anything the controls cannot express round-trips
 * as `custom`, which is lossless.
 */
export function parseCronExpression(cron: string): {
    cadence: TimeCadence;
    timeOfDay: string;
    weeklyDays: string[];
    monthDay: number;
    customCron: string;
} {
    const fallback = {
        cadence: 'custom' as TimeCadence,
        timeOfDay: '09:00',
        weeklyDays: ['1'],
        monthDay: 1,
        customCron: cron.trim(),
    };

    const parts = cron.trim().split(/\s+/);
    if (parts.length !== 5) return fallback;

    const [minute, hour, dayOfMonth, month, dayOfWeek] = parts;
    if (month !== '*') return fallback;

    if (hour === '*' && minute === '0' && dayOfMonth === '*' && dayOfWeek === '*') {
        return { ...fallback, cadence: 'hourly' };
    }

    // Every other cadence pins a wall-clock time; a wildcard or step in either
    // field is something only the raw expression can say.
    if (!/^\d{1,2}$/.test(hour) || !/^\d{1,2}$/.test(minute)) return fallback;
    const timeOfDay = `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`;

    if (dayOfMonth === '*' && dayOfWeek === '*') {
        return { ...fallback, cadence: 'daily', timeOfDay };
    }
    if (dayOfMonth === '*' && dayOfWeek === '1-5') {
        return { ...fallback, cadence: 'weekdays', timeOfDay };
    }
    if (dayOfMonth === '*' && /^[0-6](,[0-6])*$/.test(dayOfWeek)) {
        return { ...fallback, cadence: 'weekly', timeOfDay, weeklyDays: dayOfWeek.split(',') };
    }
    if (dayOfWeek === '*' && /^\d{1,2}$/.test(dayOfMonth)) {
        const day = Number(dayOfMonth);
        if (day >= 1 && day <= 31) {
            return { ...fallback, cadence: 'monthly', timeOfDay, monthDay: day };
        }
    }

    return fallback;
}

export type ScheduleTargetKind = 'workflow' | 'agent' | 'unknown';
export type ScheduleConfigDetail = {
    label: string;
    value: string;
};
export type DataOperation = 'INSERT' | 'UPDATE' | 'DELETE';

export function getScheduleTargetKind(schedule: Schedule): ScheduleTargetKind {
    if (schedule.workflow_name || schedule.workflow_id) return 'workflow';
    if (schedule.agent_name || schedule.agent_id) return 'agent';
    return 'unknown';
}

export function getScheduleTargetName(schedule: Schedule): string {
    // `agent_name` on a default-assistant trigger is the wire selector, which
    // is not a thing to show anyone. Every other target already carries the
    // name it is known by.
    if (schedule.targets_pod_default) return DEFAULT_RESPONDER_NAME;
    return schedule.workflow_name || schedule.agent_name || schedule.workflow_id || schedule.agent_id || 'Unknown target';
}

export function formatScheduleType(value: unknown): string {
    const type = typeof value === 'string' ? value : '';
    if (type === 'TIME') return 'Time schedule';
    if (type === 'WEBHOOK') return 'App event';
    if (type === 'DATASTORE') return 'Data event';
    return type || 'Schedule';
}

function formatConfigValue(value: unknown): string {
    if (typeof value === 'string') return value;
    if (typeof value === 'number' || typeof value === 'boolean') return String(value);
    if (Array.isArray(value)) return value.map(formatConfigValue).filter(Boolean).join(', ');
    return '';
}

function parseMaybeJsonObject(value: unknown): Record<string, unknown> | null {
    if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>;
    if (typeof value !== 'string') return null;

    try {
        const parsed = JSON.parse(value);
        return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
            ? parsed as Record<string, unknown>
            : null;
    } catch {
        return null;
    }
}

function flattenScheduleConfig(value: unknown): Record<string, unknown> {
    const result: Record<string, unknown> = {};

    const visit = (current: unknown) => {
        const parsed = parseMaybeJsonObject(current);
        if (!parsed) return;

        for (const [key, nestedValue] of Object.entries(parsed)) {
            result[key] = nestedValue;
            if (nestedValue && typeof nestedValue === 'object' && !Array.isArray(nestedValue)) {
                visit(nestedValue);
            } else if (typeof nestedValue === 'string' && nestedValue.trim().startsWith('{')) {
                visit(nestedValue);
            }
        }
    };

    visit(value);
    return result;
}

function getScheduleConfig(schedule: Schedule): Record<string, unknown> {
    const flattened = flattenScheduleConfig(schedule.config);
    const scheduleRecord = schedule as unknown as Record<string, unknown>;
    const topLevelKeys = [
        'cron_expression',
        'cronExpression',
        'cron',
        'expression',
        'schedule_cron',
        'scheduleCron',
        'time',
        'run_at',
        'runAt',
        'starts_at',
        'startsAt',
        'start_time',
        'startTime',
        'time_of_day',
        'timeOfDay',
        'timezone',
        'time_zone',
        'timeZone',
        'tz',
    ];

    for (const key of topLevelKeys) {
        if (flattened[key] === undefined && scheduleRecord[key] !== undefined) {
            flattened[key] = scheduleRecord[key];
        }
    }

    return flattened;
}

function firstConfigValue(record: Record<string, unknown>, keys: string[]): string {
    for (const key of keys) {
        const value = formatConfigValue(record[key]).trim();
        if (value) return value;
    }
    return '';
}

function formatTimeValue(value: string): string {
    const trimmed = value.trim();
    if (!trimmed) return '';
    const timeOnly = trimmed.match(/^(\d{1,2}):(\d{2})(?::\d{2})?$/);
    if (timeOnly) return `${timeOnly[1].padStart(2, '0')}:${timeOnly[2]}`;

    const timestamp = Date.parse(trimmed);
    if (!Number.isNaN(timestamp)) {
        return new Intl.DateTimeFormat('en', {
            hour: '2-digit',
            minute: '2-digit',
            hourCycle: 'h23',
        }).format(new Date(timestamp));
    }

    return trimmed;
}

const CRON_DAY_NAMES: Record<string, string> = {
    '0': 'Sundays', '7': 'Sundays', '1': 'Mondays', '2': 'Tuesdays',
    '3': 'Wednesdays', '4': 'Thursdays', '5': 'Fridays', '6': 'Saturdays',
    SUN: 'Sundays', MON: 'Mondays', TUE: 'Tuesdays', WED: 'Wednesdays',
    THU: 'Thursdays', FRI: 'Fridays', SAT: 'Saturdays',
};

export function describeCron(cron: string): string {
    const parts = cron.trim().split(/\s+/);
    if (parts.length < 5) return cron;

    const [minute, hour, dayOfMonth, , dayOfWeek] = parts;
    const hasFixedTime = /^\d+$/.test(hour) && /^\d+$/.test(minute);
    const timeStr = hasFixedTime
        ? `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
        : null;

    if (hour === '*' && minute === '0') return 'Every hour';
    if (hour.startsWith('*/') && minute === '0') return `Every ${hour.slice(2)} hours`;
    if (hour === '*' && minute.startsWith('*/')) return `Every ${minute.slice(2)} min`;
    if (dayOfWeek === '1-5' && timeStr) return `Weekdays at ${timeStr}`;
    if (dayOfWeek === '0,6' && timeStr) return `Weekends at ${timeStr}`;
    if (dayOfWeek === '*' && dayOfMonth === '*' && timeStr) return `Daily at ${timeStr}`;
    if (dayOfMonth === '*' && timeStr) {
        const dayName = CRON_DAY_NAMES[dayOfWeek.toUpperCase()];
        if (dayName) return `${dayName} at ${timeStr}`;
    }
    if (dayOfMonth === '1' && timeStr) return `1st of each month at ${timeStr}`;

    return cron;
}

export function describeScheduleConfig(schedule: Schedule): string {
    const config = getScheduleConfig(schedule);
    if (schedule.schedule_type === 'TIME') {
        const cron = firstConfigValue(config, [
            'cron_expression',
            'cronExpression',
            'cron',
            'expression',
            'schedule_cron',
            'scheduleCron',
        ]);
        const time = firstConfigValue(config, [
            'time',
            'time_of_day',
            'timeOfDay',
            'run_at',
            'runAt',
            'starts_at',
            'startsAt',
            'start_time',
            'startTime',
        ]);
        const timezone = firstConfigValue(config, ['timezone', 'time_zone', 'timeZone', 'tz']);
        const cadence = cron ? describeCron(cron) : time ? `Once at ${formatTimeValue(time)}` : 'Timing pending';
        return timezone ? `${cadence} · ${timezone}` : cadence;
    }

    if (schedule.schedule_type === 'WEBHOOK') {
        const connectorId = formatConfigValue(config.connector_id);
        const triggerId = schedule.connector_trigger_id || formatConfigValue(config.connector_trigger_id);
        if (connectorId && triggerId) return `On ${triggerId} from ${connectorId}`;
        if (triggerId) return `On ${triggerId}`;
        if (connectorId) return `On events from ${connectorId}`;
        return schedule.connector_trigger_id
            ? `On ${schedule.connector_trigger_id}`
            : 'Webhook or app event';
    }

    if (schedule.schedule_type === 'DATASTORE') {
        const tableName = typeof config.table_name === 'string' ? config.table_name : '';
        const operations = Array.isArray(config.operations) ? config.operations.join(', ') : '';
        if (tableName && operations) return `On ${operations.toLowerCase()} in ${tableName}`;
        if (tableName) return `On changes in ${tableName}`;
        return 'On data change';
    }

    return 'Configured schedule';
}

const CRON_KEYS = ['cron_expression', 'cronExpression', 'cron', 'expression', 'schedule_cron', 'scheduleCron'];
const DATA_OPERATIONS: DataOperation[] = ['INSERT', 'UPDATE', 'DELETE'];

/** What a TIME trigger fires on, in the shape the cadence controls speak. */
export function getScheduleTimeConfig(schedule: Schedule): { cron: string; timezone: string } {
    const config = getScheduleConfig(schedule);
    return {
        cron: firstConfigValue(config, CRON_KEYS),
        timezone: firstConfigValue(config, ['timezone', 'time_zone', 'timeZone', 'tz']) || 'UTC',
    };
}

/** What a DATASTORE trigger watches. */
export function getScheduleDatastoreConfig(schedule: Schedule): {
    tableName: string;
    operations: DataOperation[];
} {
    const config = getScheduleConfig(schedule);
    const operations = Array.isArray(config.operations)
        ? config.operations
            .map((operation) => String(operation).toUpperCase())
            .filter((operation): operation is DataOperation => DATA_OPERATIONS.includes(operation as DataOperation))
        : [];
    return {
        tableName: typeof config.table_name === 'string' ? config.table_name : '',
        operations,
    };
}

/** Which app event a WEBHOOK trigger listens to. Read-only after creation. */
export function getScheduleWebhookConfig(schedule: Schedule): {
    connectorId: string;
    triggerId: string;
} {
    const config = getScheduleConfig(schedule);
    return {
        connectorId: formatConfigValue(config.connector_id),
        triggerId: schedule.connector_trigger_id || formatConfigValue(config.connector_trigger_id),
    };
}

export function getScheduleConfigDetails(schedule: Schedule): ScheduleConfigDetail[] {
    const config = getScheduleConfig(schedule);

    if (schedule.schedule_type === 'TIME') {
        const mode = formatConfigValue(config.schedule_type);
        const cron = firstConfigValue(config, [
            'cron_expression',
            'cronExpression',
            'cron',
            'expression',
            'schedule_cron',
            'scheduleCron',
        ]);
        const timezone = firstConfigValue(config, ['timezone', 'time_zone', 'timeZone', 'tz']);
        const time = firstConfigValue(config, [
            'time',
            'time_of_day',
            'timeOfDay',
            'run_at',
            'runAt',
            'starts_at',
            'startsAt',
            'start_time',
            'startTime',
        ]);
        return [
            mode ? { label: 'Mode', value: mode } : null,
            cron ? { label: 'Cron', value: cron } : null,
            time ? { label: 'Time', value: formatTimeValue(time) } : null,
            timezone ? { label: 'TZ', value: timezone } : null,
        ].filter(Boolean) as ScheduleConfigDetail[];
    }

    if (schedule.schedule_type === 'WEBHOOK') {
        const connectorId = formatConfigValue(config.connector_id);
        const triggerId = schedule.connector_trigger_id || formatConfigValue(config.connector_trigger_id);
        return [
            connectorId ? { label: 'App', value: connectorId } : null,
            triggerId ? { label: 'Trigger', value: triggerId } : null,
            schedule.account_id ? { label: 'Account', value: schedule.account_id } : null,
        ].filter(Boolean) as ScheduleConfigDetail[];
    }

    if (schedule.schedule_type === 'DATASTORE') {
        const tableName = formatConfigValue(config.table_name);
        const operations = Array.isArray(config.operations) ? config.operations.join(', ') : '';
        return [
            tableName ? { label: 'Table', value: tableName } : null,
            operations ? { label: 'Ops', value: operations } : null,
        ].filter(Boolean) as ScheduleConfigDetail[];
    }

    return Object.entries(config)
        .map(([label, value]) => ({ label, value: formatConfigValue(value) }))
        .filter((detail) => detail.value);
}

/* -- Match conditions on a data trigger ------------------------------------ */

/**
 * A row of the "Only run when" builder.
 *
 * The reader picks a column, how to test it, and what to test against. The
 * wire format is an object per column keyed by operator; this flat shape is
 * what a list of editable rows needs, so the two are translated at the edges.
 */
export type ConditionOperator = 'is' | 'is_not' | 'became' | 'was' | 'changed';

export interface MatchCondition {
    column: string;
    operator: ConditionOperator;
    value: string;
}

/** Wire operator per UI operator. `changed` carries no value of its own. */
const CONDITION_WIRE_KEY: Record<ConditionOperator, string> = {
    is: 'equals',
    is_not: 'not_equals',
    became: 'to',
    was: 'from',
    changed: 'changed',
};

/**
 * Which change types each operator can ever be satisfied by. The API rejects a
 * condition no declared operation could satisfy, so the builder offers only the
 * operators the chosen operations can actually answer — a trigger that can
 * never fire should be unreachable, not a failed save.
 */
const CONDITION_OPERATOR_NEEDS: Record<ConditionOperator, DataOperation[]> = {
    is: ['INSERT', 'UPDATE', 'DELETE'],
    is_not: ['INSERT', 'UPDATE', 'DELETE'],
    became: ['INSERT', 'UPDATE'],
    was: ['UPDATE'],
    changed: ['UPDATE'],
};

export const CONDITION_OPERATOR_LABEL: Record<ConditionOperator, string> = {
    is: 'is',
    is_not: 'is not',
    became: 'became',
    was: 'was',
    changed: 'changed',
};

/** Operators that take no value — the test is the change itself. */
export function conditionTakesValue(operator: ConditionOperator): boolean {
    return operator !== 'changed';
}

export function availableConditionOperators(operations: DataOperation[]): ConditionOperator[] {
    return (Object.keys(CONDITION_OPERATOR_NEEDS) as ConditionOperator[]).filter((operator) =>
        CONDITION_OPERATOR_NEEDS[operator].some((operation) => operations.includes(operation)),
    );
}

/**
 * Coerce a typed-in value to the shape the stored row actually holds.
 *
 * Conditions compare against the row as JSON, so a number column holds `5` and
 * never `"5"`. Sending the string would produce a condition that silently never
 * matches — the failure this whole feature exists to remove.
 */
function coerceConditionValue(raw: string, columnType?: string): unknown {
    const value = raw.trim();
    if (columnType === 'INTEGER' || columnType === 'FLOAT' || columnType === 'SERIAL') {
        const parsed = Number(value);
        return value !== '' && Number.isFinite(parsed) ? parsed : value;
    }
    if (columnType === 'BOOLEAN') {
        if (/^true$/i.test(value)) return true;
        if (/^false$/i.test(value)) return false;
    }
    return value;
}

/** Build the `when` block, or `undefined` when there is nothing to say. */
export function buildMatchConditions(
    conditions: MatchCondition[],
    columnTypes: Record<string, string> = {},
): Record<string, Record<string, unknown>> | undefined {
    const when: Record<string, Record<string, unknown>> = {};
    for (const condition of conditions) {
        const column = condition.column.trim();
        if (!column) continue;
        if (condition.operator === 'changed') {
            when[column] = { ...(when[column] || {}), changed: true };
            continue;
        }
        if (!condition.value.trim()) continue;
        when[column] = {
            ...(when[column] || {}),
            [CONDITION_WIRE_KEY[condition.operator]]: coerceConditionValue(
                condition.value,
                columnTypes[column],
            ),
        };
    }
    return Object.keys(when).length ? when : undefined;
}

/**
 * Read stored conditions back into rows.
 *
 * Operators this builder does not offer (`in`, `not_in`, `written`) are skipped
 * rather than mangled — a trigger authored through the CLI or a bundle keeps
 * them, and `unsupported` says so instead of the editor quietly dropping them.
 */
export function parseMatchConditions(schedule: Schedule): {
    conditions: MatchCondition[];
    unsupported: string[];
} {
    const config = getScheduleConfig(schedule);
    const when = config.when;
    const conditions: MatchCondition[] = [];
    const unsupported: string[] = [];
    if (!when || typeof when !== 'object' || Array.isArray(when)) {
        return { conditions, unsupported };
    }

    const wireToOperator = new Map<string, ConditionOperator>(
        (Object.keys(CONDITION_WIRE_KEY) as ConditionOperator[]).map((operator) => [
            CONDITION_WIRE_KEY[operator],
            operator,
        ]),
    );

    for (const [column, raw] of Object.entries(when as Record<string, unknown>)) {
        if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue;
        for (const [wireKey, value] of Object.entries(raw as Record<string, unknown>)) {
            const operator = wireToOperator.get(wireKey);
            if (!operator) {
                unsupported.push(`${column} ${wireKey}`);
                continue;
            }
            if (operator === 'changed') {
                // `changed: false` is a real condition this builder cannot draw.
                if (value !== true) unsupported.push(`${column} ${wireKey}`);
                else conditions.push({ column, operator, value: '' });
                continue;
            }
            conditions.push({ column, operator, value: value === null ? '' : String(value) });
        }
    }
    return { conditions, unsupported };
}
