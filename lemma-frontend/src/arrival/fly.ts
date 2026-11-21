/** An app ticked on the right flies across into its spot on the Lemma.
 *
 *  A copy of its line drawing leaves the row, arcs over and lands where the
 *  stage has kept its spot open, and only then is the spot drawn. Resolves
 *  at once where there is nowhere to fly to -- a phone's strip, reduced
 *  motion -- so the caller can always wait on it. */
export function flyInto(from: Element | null, slot: string): Promise<void> {
    return new Promise((resolve) => {
        if (!from || matchMedia("(prefers-reduced-motion: reduce)").matches) {
            resolve();
            return;
        }
        /* A frame for the stage to draw the spot it keeps open. */
        requestAnimationFrame(() => {
            const target = document.querySelector(`.ast [data-slot="${CSS.escape(slot)}"] .ast__tile`);
            const a = from.getBoundingClientRect();
            const b = target?.getBoundingClientRect();
            if (!b || !b.width || !a.width) {
                resolve();
                return;
            }
            const ghost = document.createElement("div");
            ghost.className = "ar-ghost";
            const drawing = from.querySelector("svg");
            if (drawing) ghost.appendChild(drawing.cloneNode(true));
            Object.assign(ghost.style, { left: `${a.left}px`, top: `${a.top}px`, width: `${a.width}px`, height: `${a.height}px` });
            document.body.appendChild(ghost);
            const dx = b.left + b.width / 2 - (a.left + a.width / 2);
            const dy = b.top + b.height / 2 - (a.top + a.height / 2);
            const scale = b.width / a.width;
            const flight = ghost.animate([
                { transform: "translate(0, 0) scale(1) rotate(0deg)" },
                { transform: `translate(${dx * 0.5}px, ${dy * 0.5 - 90}px) scale(${(1 + scale) / 2 + 0.15}) rotate(-10deg)`, offset: 0.45 },
                { transform: `translate(${dx}px, ${dy}px) scale(${scale}) rotate(0deg)` },
            ], { duration: 720, easing: "cubic-bezier(.45, 0, .25, 1)" });
            const land = () => {
                ghost.remove();
                resolve();
            };
            flight.onfinish = land;
            flight.oncancel = land;
        });
    });
}
