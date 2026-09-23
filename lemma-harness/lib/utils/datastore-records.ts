import type { Column } from '@/lib/types';
import type { FilterRule } from '@/lib/types/app';
import type { RecordFilter, RecordSort } from 'lemma-sdk';

type RecordFilterOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte' | 'ilike';
type RecordSortDirection = 'asc' | 'desc';

const RECORD_FILTER_OPERATOR: Record<Uppercase<RecordFilterOperator>, RecordFilterOperator> = {
    EQ: 'eq',
    NE: 'ne',
    GT: 'gt',
    GTE: 'gte',
    LT: 'lt',
    LTE: 'lte',
    ILIKE: 'ilike',
};

const RECORD_SORT_DIRECTION: Record<Uppercase<RecordSortDirection>, RecordSortDirection> = {
    ASC: 'asc',
    DESC: 'desc',
};

export function buildRecordQueryFilters(filters: FilterRule[] = []): RecordFilter[] {
    const queryFilters: RecordFilter[] = [];
    const operatorMap: Record<Extract<FilterRule['operator'], 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'>, RecordFilterOperator> = {
        eq: RECORD_FILTER_OPERATOR.EQ,
        ne: RECORD_FILTER_OPERATOR.NE,
        gt: RECORD_FILTER_OPERATOR.GT,
        gte: RECORD_FILTER_OPERATOR.GTE,
        lt: RECORD_FILTER_OPERATOR.LT,
        lte: RECORD_FILTER_OPERATOR.LTE,
    };

    filters.forEach((filter) => {
        switch (filter.operator) {
            case 'eq':
            case 'ne':
            case 'gt':
            case 'gte':
            case 'lt':
            case 'lte':
                queryFilters.push({ field: filter.field, op: operatorMap[filter.operator], value: filter.value });
                break;
            case 'contains':
                queryFilters.push({ field: filter.field, op: RECORD_FILTER_OPERATOR.ILIKE, value: `%${String(filter.value)}%` });
                break;
            case 'startsWith':
                queryFilters.push({ field: filter.field, op: RECORD_FILTER_OPERATOR.ILIKE, value: `${String(filter.value)}%` });
                break;
            case 'endsWith':
                queryFilters.push({ field: filter.field, op: RECORD_FILTER_OPERATOR.ILIKE, value: `%${String(filter.value)}` });
                break;
            case 'in': {
                const parts = Array.isArray(filter.value)
                    ? filter.value
                    : String(filter.value)
                        .split(',')
                        .map((part) => part.trim())
                        .filter(Boolean);

                if (parts.length <= 1) {
                    if (parts.length === 1) {
                        queryFilters.push({ field: filter.field, op: RECORD_FILTER_OPERATOR.EQ, value: parts[0] });
                    }
                }

                // Structured query filters are AND-only, so a true multi-value IN clause
                // cannot be represented safely here.
                break;
            }
            default:
                break;
        }
    });

    return queryFilters;
}

export function buildRecordQuerySort(field: string | null, direction: 'asc' | 'desc'): RecordSort[] | undefined {
    if (!field) return undefined;
    return [{
        field,
        direction: direction === 'asc' ? RECORD_SORT_DIRECTION.ASC : RECORD_SORT_DIRECTION.DESC,
    }];
}

export function getDisplayColumns(columns: Column[], primaryKeyColumn?: string): Column[] {
    if (!primaryKeyColumn) return [...columns];

    const primaryKey = columns.find((column) => column.name === primaryKeyColumn);
    if (!primaryKey) return [...columns];

    return [
        ...columns.filter((column) => column.name !== primaryKeyColumn),
        primaryKey,
    ];
}

export function sanitizeRecordPayload(
    columns: Column[],
    values: Record<string, unknown>,
    options: { omitAutoComputed?: boolean } = {}
): Record<string, unknown> {
    const columnsByName = new Map(columns.map((column) => [column.name, column] as const));

    return Object.entries(values).reduce<Record<string, unknown>>((accumulator, [key, value]) => {
        const column = columnsByName.get(key);

        if (options.omitAutoComputed && (column?.auto || column?.computed)) {
            return accumulator;
        }

        if (value === null || value === undefined) {
            return accumulator;
        }

        if (typeof value === 'string' && value.trim() === '') {
            return accumulator;
        }

        accumulator[key] = value;
        return accumulator;
    }, {});
}
