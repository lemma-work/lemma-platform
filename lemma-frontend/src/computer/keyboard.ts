interface BrowserKeyEvent {
    key: string;
    metaKey: boolean;
    ctrlKey: boolean;
    altKey: boolean;
    preventDefault(): void;
    stopPropagation(): void;
}

interface KeyboardClient {
    viewOnly: boolean;
    sendKey(key: number, code: string, down: boolean): void;
}

export function handleBrowserKeyDown(event: BrowserKeyEvent, client: KeyboardClient | null) {
    if (!client || client.viewOnly || event.altKey) return;
    const key = event.key.toLowerCase();
    if (key === "v" && (event.metaKey || event.ctrlKey)) {
        // noVNC cancels native paste unless this key stops before its canvas.
        event.stopPropagation();
        return;
    }
    if (!event.metaKey || event.ctrlKey || key.length !== 1 || !"cxa".includes(key)) return;
    event.preventDefault();
    event.stopPropagation();
    client.sendKey(0xffe3, "ControlLeft", true);
    client.sendKey(key.charCodeAt(0), "Key" + key.toUpperCase(), true);
    client.sendKey(key.charCodeAt(0), "Key" + key.toUpperCase(), false);
    client.sendKey(0xffe3, "ControlLeft", false);
}
