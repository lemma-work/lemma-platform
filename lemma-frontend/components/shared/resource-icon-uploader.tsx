'use client';

import { ChangeEvent, useMemo, useRef, useState } from 'react';
import { ImagePlus, Loader2, Sparkles, Trash2 } from '@/components/ui/icons';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { ResourceIcon } from './resource-icon';

type IconTemplate = {
    id: string;
    from: string;
    to: string;
    accent: string;
    swatchClass: string;
};

const ICON_TEMPLATES: IconTemplate[] = [
    { id: 'mint', from: 'rgb(110 231 183)', to: 'rgb(52 211 153)', accent: 'rgb(6 78 59)', swatchClass: 'resource-template-swatch-mint' },
    { id: 'sunset', from: 'rgb(252 165 165)', to: 'rgb(251 113 133)', accent: 'rgb(76 5 25)', swatchClass: 'resource-template-swatch-sunset' },
    { id: 'ocean', from: 'rgb(125 211 252)', to: 'rgb(96 165 250)', accent: 'rgb(8 47 73)', swatchClass: 'resource-template-swatch-ocean' },
    { id: 'amber', from: 'rgb(252 211 77)', to: 'rgb(245 158 11)', accent: 'rgb(69 26 3)', swatchClass: 'resource-template-swatch-amber' },
    { id: 'violet', from: 'rgb(196 181 253)', to: 'rgb(167 139 250)', accent: 'rgb(46 16 101)', swatchClass: 'resource-template-swatch-violet' },
    { id: 'slate', from: 'rgb(203 213 225)', to: 'rgb(148 163 184)', accent: 'rgb(15 23 42)', swatchClass: 'resource-template-swatch-slate' },
];

function initialsFromName(value: string, fallback: string): string {
    const trimmed = value.trim();
    if (!trimmed) return fallback.slice(0, 2).toUpperCase();
    const words = trimmed.split(/\s+/).slice(0, 2);
    const initials = words.map((word) => word.charAt(0).toUpperCase()).join('');
    return initials || fallback.slice(0, 2).toUpperCase();
}

function hash(value: string): number {
    let result = 0;
    for (let i = 0; i < value.length; i += 1) {
        result = (result << 5) - result + value.charCodeAt(i);
        result |= 0;
    }
    return Math.abs(result);
}

function createTemplateSvg(template: IconTemplate, initials: string): string {
    return `<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="g" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="${template.from}" />
      <stop offset="100%" stop-color="${template.to}" />
    </linearGradient>
  </defs>
  <rect x="0" y="0" width="512" height="512" rx="120" fill="url(#g)" />
  <text x="50%" y="54%" text-anchor="middle" dominant-baseline="middle"
    font-family="Inter, Arial, sans-serif" font-weight="700" font-size="180" fill="${template.accent}">
    ${initials}
  </text>
</svg>`;
}

function templateFileName(kind: string): string {
    return `${kind}-icon-${Date.now()}.svg`;
}

interface ResourceIconUploaderProps {
    kind: 'pod' | 'agent' | 'assistant' | 'function' | 'flow';
    name: string;
    value?: string | null;
    onChange: (iconUrl: string | null) => void;
    compact?: boolean;
    emphasis?: 'default' | 'hero';
    iconClassName?: string;
    showTemplates?: boolean;
    disabled?: boolean;
    className?: string;
}

export function ResourceIconUploader({
    kind,
    name,
    value,
    onChange,
    compact = true,
    emphasis = 'default',
    iconClassName,
    showTemplates = false,
    disabled = false,
    className,
}: ResourceIconUploaderProps) {
    const inputRef = useRef<HTMLInputElement>(null);
    const [isUploading, setIsUploading] = useState(false);
    const isBusy = isUploading || disabled;
    const isHero = emphasis === 'hero';
    const seededTemplate = useMemo(
        () => ICON_TEMPLATES[hash(`${kind}:${name}`) % ICON_TEMPLATES.length],
        [kind, name]
    );

    const uploadIcon = async (file: File) => {
        setIsUploading(true);
        try {
            const response = await getLemmaClient().icons.upload(file);
            onChange(response.icon_url);
            toast.success('Icon updated');
        } catch (error) {
            console.error('Failed to upload icon:', error);
            toast.error('Failed to upload icon');
        } finally {
            setIsUploading(false);
        }
    };

    const handleFileSelection = async (event: ChangeEvent<HTMLInputElement>) => {
        const file = event.target.files?.[0];
        if (!file) return;
        await uploadIcon(file);
        if (inputRef.current) {
            inputRef.current.value = '';
        }
    };

    const handleTemplateUpload = async (template: IconTemplate) => {
        const initials = initialsFromName(name, kind);
        const svg = createTemplateSvg(template, initials);
        const file = new File([svg], templateFileName(kind), { type: 'image/svg+xml' });
        await uploadIcon(file);
    };

    return (
        <div className={cn(compact ? 'space-y-1.5' : 'space-y-3', isHero && 'space-y-3.5', className)}>
            <div className={cn('flex flex-wrap items-center', compact ? 'gap-2' : 'gap-3', isHero && 'gap-4')}>
                <div
                    className={cn(
                        'relative shrink-0',
                        isHero &&
                            'resource-icon-hero-shell p-2'
                    )}
                >
                    {isHero && <div aria-hidden="true" className="resource-icon-hero-glow pointer-events-none absolute -inset-3 animate-pulse" />}
                    <ResourceIcon
                        iconUrl={value}
                        alt={`${kind} icon`}
                        label={name}
                        className={cn(
                            compact ? 'h-10 w-10' : 'h-14 w-14',
                            isHero && 'resource-icon-hero-image relative h-20 w-20 rounded-lg',
                            iconClassName
                        )}
                    />
                    {isHero && (
                        <span className="resource-icon-hero-badge absolute -right-1 -top-1 inline-flex h-6 w-6 items-center justify-center rounded-full">
                            <Sparkles className="h-3.5 w-3.5" />
                        </span>
                    )}
                </div>

                <div className="flex flex-wrap items-center gap-2">
                    <input
                        ref={inputRef}
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={handleFileSelection}
                    />

                    <Button
                        type="button"
                        size={compact ? 'sm' : 'sm'}
                        variant={compact && !isHero ? 'ghost' : 'outline'}
                        className={cn(
                            compact ? 'h-8 gap-1 px-2 text-xs text-[var(--text-secondary)]' : 'gap-1.5',
                            isHero && 'h-9 rounded-lg border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-3 text-xs text-[var(--button-secondary-fg)]'
                        )}
                        disabled={isBusy}
                        onClick={() => inputRef.current?.click()}
                    >
                        {isUploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <ImagePlus className="h-3.5 w-3.5" />}
                        Upload
                    </Button>

                    <Button
                        type="button"
                        size={compact ? 'sm' : 'sm'}
                        variant={compact && !isHero ? 'ghost' : 'outline'}
                        className={cn(
                            compact ? 'h-8 gap-1 px-2 text-xs text-[var(--text-secondary)]' : 'gap-1.5',
                            isHero && 'h-9 rounded-lg border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-3 text-xs text-[var(--button-secondary-fg)]'
                        )}
                        disabled={isBusy}
                        onClick={() => handleTemplateUpload(seededTemplate)}
                    >
                        <Sparkles className="h-3.5 w-3.5" />
                        Default
                    </Button>

                    {value && (
                        <Button
                            type="button"
                            size={compact ? 'sm' : 'sm'}
                            variant="ghost"
                            className={cn('gap-1.5 text-[var(--state-error)]', compact && 'h-8 px-2 text-xs', isHero && 'h-9 rounded-lg px-3')}
                            disabled={isBusy}
                            onClick={() => onChange(null)}
                        >
                            <Trash2 className="h-3.5 w-3.5" />
                            Remove
                        </Button>
                    )}
                </div>
            </div>

            {showTemplates && (
                <div className="flex items-center gap-2">
                    <span className="text-xs text-[var(--text-tertiary)]">Templates</span>
                    {ICON_TEMPLATES.map((template) => (
                        <button
                            key={template.id}
                            type="button"
                            disabled={isBusy}
                            onClick={() => handleTemplateUpload(template)}
                            className={cn(
                                'h-5 w-5 rounded-full border border-[color:var(--chip-border)] transition-transform hover:scale-105 disabled:cursor-not-allowed disabled:opacity-60',
                                template.swatchClass
                            )}
                            aria-label={`Use ${template.id} icon template`}
                        />
                    ))}
                </div>
            )}
        </div>
    );
}
