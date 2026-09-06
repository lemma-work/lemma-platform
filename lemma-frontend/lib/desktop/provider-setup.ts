"use client";

import { useEffect, useRef, useState } from "react";
import { configureAiProvider, discoverProviderModels, type AiProfileDraft } from "./local-capabilities";

export type ProviderPreset = {
    id: string;
    title: string;
    hint: string;
    protocol: AiProfileDraft["protocol"];
    baseUrl: string;
    needsKey: boolean;
};

export function useLocalProviderSetup() {
    const [preset, setPreset] = useState<ProviderPreset | null>(null);
    const [apiKey, saveApiKey] = useState("");
    const [models, setModels] = useState<string[]>([]);
    const [model, saveModel] = useState("");
    const [listing, setListing] = useState(false);
    const [applying, setApplying] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const generation = useRef(0);
    const applyingNow = useRef(false);
    const discoveringNow = useRef(false);

    useEffect(() => () => { generation.current += 1; }, []);

    const invalidate = () => {
        generation.current += 1;
        discoveringNow.current = false;
        setModels([]);
        saveModel("");
        setListing(false);
        setError(null);
    };

    const selectPreset = (next: ProviderPreset) => {
        if (applyingNow.current) return;
        invalidate();
        setPreset(next);
        saveApiKey("");
    };

    const setApiKey = (value: string) => {
        if (applyingNow.current) return;
        invalidate();
        saveApiKey(value);
    };

    const setModel = (value: string) => {
        if (!applyingNow.current && models.includes(value)) saveModel(value);
    };

    const draft = (): AiProfileDraft | null => preset ? {
        protocol: preset.protocol,
        base_url: preset.baseUrl,
        default_model: model,
        models,
        vision_models: [],
        allow_private_network: false,
    } : null;

    const listModels = async () => {
        const candidate = draft();
        if (!candidate || applyingNow.current || (preset?.needsKey && !apiKey.trim())) return;
        const request = ++generation.current;
        discoveringNow.current = true;
        setListing(true);
        setModels([]);
        saveModel("");
        setError(null);
        try {
            const found = await discoverProviderModels({ ...candidate, default_model: "", models: [] }, apiKey);
            if (request !== generation.current) return;
            if (!found.length) {
                setError("That provider answered, but reported no models.");
                return;
            }
            setModels(found);
            saveModel(found.includes(model) ? model : found[0]);
        } catch (failure) {
            if (request === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
        } finally {
            if (request === generation.current) {
                discoveringNow.current = false;
                setListing(false);
            }
        }
    };

    const apply = async (): Promise<boolean> => {
        const candidate = draft();
        if (!candidate || !models.includes(model) || applyingNow.current || discoveringNow.current) return false;
        applyingNow.current = true;
        const request = ++generation.current;
        setApplying(true);
        setError(null);
        try {
            await configureAiProvider(candidate, apiKey);
            return request === generation.current;
        } catch (failure) {
            if (request === generation.current) setError(failure instanceof Error ? failure.message : String(failure));
            return false;
        } finally {
            applyingNow.current = false;
            if (request === generation.current) setApplying(false);
        }
    };

    return { preset, selectPreset, apiKey, setApiKey, models, model, setModel, listing, applying, error, listModels, apply };
}
