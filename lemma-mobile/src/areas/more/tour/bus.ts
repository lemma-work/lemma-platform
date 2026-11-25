/** "Show me around", asked for from somewhere other than Home: the account
 *  screen's Help. The tour runs over Home, so the asker goes back there and
 *  Home's `TourHost` takes it up. Held until somebody listens, so a request
 *  made a frame before Home is back is not lost. */

type Listener = () => void;

const listeners = new Set<Listener>();
let waiting = false;

export function askForTour(): void {
    if (listeners.size === 0) {
        waiting = true;
        return;
    }
    for (const listener of listeners) listener();
}

export function onTourAsked(listener: Listener): () => void {
    listeners.add(listener);
    if (waiting) {
        waiting = false;
        listener();
    }
    return () => {
        listeners.delete(listener);
    };
}
