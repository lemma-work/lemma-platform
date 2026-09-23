export type ImportRow = { id: number; company: string; email: string; joined: string };
export const originalRows: ImportRow[] = [
    { id: 1, company: "Aster Studio", email: "hello@aster.example", joined: "14/09/2026" },
    { id: 2, company: "Moss & Co", email: "ops@moss.example", joined: "2026-09-15" },
    { id: 3, company: "Wren Supply", email: "team.wren.example", joined: "16/09/2026" },
    { id: 4, company: "Fern Works", email: "hello@fern.example", joined: "2026-09-17" },
    { id: 5, company: "Oak House", email: "ops@oak.example", joined: "18/09/2026" },
];
export function validDate(value: string) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const date = new Date(value + "T00:00:00Z");
    return !Number.isNaN(date.valueOf()) && date.toISOString().slice(0, 10) === value;
}
export function normalizeDate(value: string) {
    const match = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(value);
    if (!match) return value;
    const candidate = `${match[3]}-${match[2]}-${match[1]}`;
    return validDate(candidate) ? candidate : value;
}
export function validateRows(rows: ImportRow[]) {
    return rows.flatMap(row => [
        ...(!row.company.trim() ? [{ row: row.id, field: "company", message: "Company is required." }] : []),
        ...(!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(row.email) ? [{ row: row.id, field: "email", message: "Use a complete email address." }] : []),
        ...(!validDate(row.joined) ? [{ row: row.id, field: "joined", message: "Use a valid date in YYYY-MM-DD format." }] : []),
    ]);
}
