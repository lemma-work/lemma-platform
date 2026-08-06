import {
    Braces,
    Calendar,
    CalendarClock,
    CheckSquare,
    FileText,
    Hash,
    KeyRound,
    Link2,
    Tag,
    TextT,
    User,
    Waypoints,
    type LemmaIcon,
} from '@/components/ui/icons';

/**
 * A column's type, in the width a bounded header can spare.
 *
 * The header used to print the type as a word in a filled chip — "datetime"
 * beside `created_at`. That reads fine in a column as wide as its longest value
 * and not at all in a column the reader has narrowed to fit six of them on the
 * screen. The glyph says the same thing in 14px; the word is still there, in
 * the header's tooltip, for anyone who wants it spelled out.
 */
const ICON_BY_TYPE: Record<string, LemmaIcon> = {
    TEXT: TextT,
    INTEGER: Hash,
    FLOAT: Hash,
    SERIAL: Hash,
    BOOLEAN: CheckSquare,
    DATE: Calendar,
    DATETIME: CalendarClock,
    ENUM: Tag,
    JSON: Braces,
    UUID: KeyRound,
    USER: User,
    LINK: Link2,
    FILE_PATH: FileText,
    VECTOR: Waypoints,
};

export function ColumnTypeIcon({ type, className }: { type: string; className?: string }) {
    const Icon = ICON_BY_TYPE[String(type).toUpperCase()] ?? TextT;

    return (
        <Icon
            className={className ?? 'h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]'}
            aria-hidden="true"
        />
    );
}
