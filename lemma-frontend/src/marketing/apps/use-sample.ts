"use client";
import { useEffect, useState } from "react";
export function useSample<T>(key: string, initial: T) {
    const [state, setState] = useState<T>(() => {
        try { const saved = sessionStorage.getItem(key); if (saved) return JSON.parse(saved) as T; } catch { /* Keep the sample usable. */ }
        return initial;
    });
    const [failed, setFailed] = useState(false);
    useEffect(() => { try { sessionStorage.setItem(key, JSON.stringify(state)); } catch { setFailed(true); } }, [key, state]);
    return [state, setState, failed] as const;
}
