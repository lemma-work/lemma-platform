import type { FilterRule } from '@/lib/types/app';

import { hasOwnProperty } from './file-helpers';

export const ROUTE_FILTER_OPERATORS: Partial<Record<string, FilterRule['operator']>> = {
    eq: 'eq',
    ne: 'ne',
    gt: 'gt',
    gte: 'gte',
    lt: 'lt',
    lte: 'lte',
};

export function normalizeLikeRouteFilter(
    field: string,
    value: unknown,
): FilterRule {
    const rawValue = typeof value === 'string' ? value : String(value ?? '');
    const startsWithWildcard = rawValue.startsWith('%');
    const endsWithWildcard = rawValue.endsWith('%');

    if (startsWithWildcard && endsWithWildcard && rawValue.length > 1) {
        return {
            field,
            operator: 'contains',
            value: rawValue.slice(1, -1),
        };
    }

    if (startsWithWildcard) {
        return {
            field,
            operator: 'endsWith',
            value: rawValue.slice(1),
        };
    }

    if (endsWithWildcard) {
        return {
            field,
            operator: 'startsWith',
            value: rawValue.slice(0, -1),
        };
    }

    return {
        field,
        operator: 'contains',
        value: rawValue,
    };
}

export function parseTableRouteFilter(value: string): FilterRule | null {
    try {
        const parsed = JSON.parse(value);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

        const record = parsed as Record<string, unknown>;
        const field = typeof record.field === 'string' ? record.field.trim() : '';
        const op = typeof record.op === 'string' ? record.op.trim().toLowerCase() : '';
        if (!field || !op || !hasOwnProperty(record, 'value')) return null;

        if (op === 'like' || op === 'ilike') {
            return normalizeLikeRouteFilter(field, record.value);
        }

        const operator = ROUTE_FILTER_OPERATORS[op];
        if (!operator) return null;

        return {
            field,
            operator,
            value: record.value,
        };
    } catch {
        return null;
    }
}

export function parseTableRouteFilters(values: string[]): FilterRule[] {
    return values
        .map(parseTableRouteFilter)
        .filter((filter): filter is FilterRule => filter !== null);
}
