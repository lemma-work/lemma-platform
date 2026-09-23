/**
 * Utility functions for managing enum option colors
 */

// Vibrant color palette for enum tags
export const ENUM_COLORS = [
    { name: 'blue', bg: 'bg-blue-100', text: 'text-blue-700', border: 'border-blue-300', hover: 'hover:bg-blue-200' },
    { name: 'green', bg: 'bg-green-100', text: 'text-green-700', border: 'border-green-300', hover: 'hover:bg-green-200' },
    { name: 'purple', bg: 'bg-purple-100', text: 'text-purple-700', border: 'border-purple-300', hover: 'hover:bg-purple-200' },
    { name: 'orange', bg: 'bg-orange-100', text: 'text-orange-700', border: 'border-orange-300', hover: 'hover:bg-orange-200' },
    { name: 'pink', bg: 'bg-pink-100', text: 'text-pink-700', border: 'border-pink-300', hover: 'hover:bg-pink-200' },
    { name: 'indigo', bg: 'bg-indigo-100', text: 'text-indigo-700', border: 'border-indigo-300', hover: 'hover:bg-indigo-200' },
    { name: 'teal', bg: 'bg-teal-100', text: 'text-teal-700', border: 'border-teal-300', hover: 'hover:bg-teal-200' },
    { name: 'amber', bg: 'bg-amber-100', text: 'text-amber-700', border: 'border-amber-300', hover: 'hover:bg-amber-200' },
    { name: 'cyan', bg: 'bg-cyan-100', text: 'text-cyan-700', border: 'border-cyan-300', hover: 'hover:bg-cyan-200' },
    { name: 'rose', bg: 'bg-rose-100', text: 'text-rose-700', border: 'border-rose-300', hover: 'hover:bg-rose-200' },
] as const;

export type EnumColor = typeof ENUM_COLORS[number];

/**
 * Get a consistent color for an enum option based on its value or index
 */
export function getEnumColor(value: string, allOptions?: string[]): EnumColor {
    if (allOptions) {
        const index = allOptions.indexOf(value);
        if (index !== -1) {
            return ENUM_COLORS[index % ENUM_COLORS.length];
        }
    }

    // Fallback: hash the value to get consistent color
    const hash = value.split('').reduce((acc, char) => {
        return char.charCodeAt(0) + ((acc << 5) - acc);
    }, 0);

    return ENUM_COLORS[Math.abs(hash) % ENUM_COLORS.length];
}

/**
 * Get color for an option by index
 */
export function getEnumColorByIndex(index: number): EnumColor {
    return ENUM_COLORS[index % ENUM_COLORS.length];
}
