'use client';

import {
    FileText,
    FileImage,
    FileVideo,
    FileAudio,
    FileCode,
    FileSpreadsheet,
    File,
    FileJson,
    FileArchive,
    Presentation,
} from '@/components/ui/icons';
import { cn } from '@/lib/utils';

interface FileTypeIconProps {
    filename: string;
    className?: string;
    size?: 'sm' | 'md' | 'lg' | 'xl';
}

const sizeClasses = {
    sm: 'w-4 h-4',
    md: 'w-5 h-5',
    lg: 'w-8 h-8',
    xl: 'w-12 h-12',
};

const iconColors = {
    document: 'text-[var(--state-info)]',
    spreadsheet: 'text-[var(--state-success)]',
    presentation: 'text-[var(--state-warning)]',
    pdf: 'text-[var(--state-error)]',
    image: 'text-[var(--state-info)]',
    video: 'text-[var(--state-error)]',
    audio: 'text-[var(--state-info)]',
    code: 'text-[var(--state-success)]',
    json: 'text-[var(--state-warning)]',
    archive: 'text-[var(--state-warning)]',
    text: 'text-[var(--text-tertiary)]',
    default: 'text-[var(--text-tertiary)]',
};

export function getFileType(filename: string): keyof typeof iconColors {
    const ext = filename.split('.').pop()?.toLowerCase() || '';

    // Documents
    if (['doc', 'docx', 'odt', 'rtf'].includes(ext)) return 'document';
    if (['xls', 'xlsx', 'csv', 'ods'].includes(ext)) return 'spreadsheet';
    if (['ppt', 'pptx', 'odp'].includes(ext)) return 'presentation';
    if (ext === 'pdf') return 'pdf';

    // Media
    if (['jpg', 'jpeg', 'png', 'gif', 'svg', 'webp', 'bmp', 'ico'].includes(ext)) return 'image';
    if (['mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'mkv'].includes(ext)) return 'video';
    if (['mp3', 'wav', 'ogg', 'flac', 'm4a', 'aac'].includes(ext)) return 'audio';

    // Code
    if (['js', 'ts', 'jsx', 'tsx', 'py', 'rb', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'hpp', 'cs', 'php', 'swift', 'kt'].includes(ext)) return 'code';
    if (['json', 'yaml', 'yml', 'toml'].includes(ext)) return 'json';
    if (['html', 'css', 'scss', 'sass', 'less'].includes(ext)) return 'code';

    // Archives
    if (['zip', 'rar', '7z', 'tar', 'gz', 'bz2'].includes(ext)) return 'archive';

    // Text
    if (['txt', 'md', 'markdown', 'log'].includes(ext)) return 'text';

    return 'default';
}

export function FileTypeIcon({ filename, className, size = 'md' }: FileTypeIconProps) {
    const fileType = getFileType(filename);
    const sizeClass = sizeClasses[size];
    const colorClass = iconColors[fileType];

    const iconProps = {
        className: cn(sizeClass, colorClass, className),
    };

    switch (fileType) {
        case 'document':
        case 'pdf':
        case 'text':
            return <FileText {...iconProps} />;
        case 'spreadsheet':
            return <FileSpreadsheet {...iconProps} />;
        case 'presentation':
            return <Presentation {...iconProps} />;
        case 'image':
            return <FileImage {...iconProps} />;
        case 'video':
            return <FileVideo {...iconProps} />;
        case 'audio':
            return <FileAudio {...iconProps} />;
        case 'code':
            return <FileCode {...iconProps} />;
        case 'json':
            return <FileJson {...iconProps} />;
        case 'archive':
            return <FileArchive {...iconProps} />;
        default:
            return <File {...iconProps} />;
    }
}

// File size formatter
export function formatFileSize(bytes?: number): string {
    if (bytes === undefined || bytes === null) return '—';
    if (bytes === 0) return '0 B';

    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));

    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
}

// Date formatter
export function formatDate(dateString?: string): string {
    if (!dateString) return '—';

    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24));

    if (diffDays === 0) return 'Today';
    if (diffDays === 1) return 'Yesterday';
    if (diffDays < 7) return `${diffDays} days ago`;

    return date.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: date.getFullYear() !== now.getFullYear() ? 'numeric' : undefined,
    });
}
