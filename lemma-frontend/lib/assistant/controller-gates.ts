export function resolveAssistantControllerGates(
    isProviderEnabled: boolean,
    shouldAutoLoad: boolean,
) {
    return {
        enabled: isProviderEnabled,
        autoLoad: isProviderEnabled && shouldAutoLoad,
    };
}
