export class BrowserTransport {
    static clients: BrowserTransport[] = [];
    listeners = new Map<string, () => void>();
    canvas: HTMLCanvasElement;
    viewOnly = false;
    scaleViewport = true;
    background = "";

    constructor(surface: HTMLElement) {
        this.canvas = surface.ownerDocument.createElement("canvas");
        this.canvas.tabIndex = 0;
        surface.append(this.canvas);
        BrowserTransport.clients.push(this);
    }

    addEventListener(name: string, callback: () => void) { this.listeners.set(name, callback); }
    emit(name: string) { this.listeners.get(name)?.(); }
    disconnect() { this.canvas.remove(); this.emit("disconnect"); }
    sendKey() {}
    clipboardPasteFrom() {}
}
