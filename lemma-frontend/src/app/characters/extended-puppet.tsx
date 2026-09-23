"use client";

import { useEffect, useId, useRef } from "react";
import type { Mood } from "./loop/loop-puppet";
import styles from "./loop/page.module.css";

import { EXTENDED, type NewCharacter } from "./extended-cast";
import { createLife, lifeGain, SMALL_ABOVE, TEMPO } from "./life";
type Point = readonly [number, number];

const damp = (a: number, b: number, rate: number, dt: number) => a + (b - a) * (1 - Math.exp(-rate * dt));
const rotate = (angle: number, point: Point) => `rotate(${angle} ${point[0]} ${point[1]})`;

/** How big this is actually drawn, in CSS pixels. It decides two things:
 *  how far the ambient motion has to be exaggerated to stay visible, and
 *  which copy of the art to fetch — a 1254-square render is wasted on a
 *  rail mark, and twenty-one of them is what made the gallery cost 19MB. */
export function ExtendedPuppet({ character, mood, greeting, paused, pixels = 620 }: { character: NewCharacter; mood: Mood; greeting: number; paused: boolean; pixels?: number }) {
    const gain = lifeGain(pixels), detail = pixels <= SMALL_ABOVE ? "-sm" : "";
    const rig = EXTENDED[character];
    const id = useId().replaceAll(":", "");
    const host = useRef<HTMLDivElement>(null);
    const root = useRef<SVGGElement>(null);
    const torso = useRef<SVGGElement>(null);
    const left = useRef<SVGGElement>(null), right = useRef<SVGGElement>(null);
    const leftFoot = useRef<SVGGElement>(null), rightFoot = useRef<SVGGElement>(null);
    const pupils = useRef<(SVGGElement | null)[]>([]);
    const lowers = useRef<(SVGPathElement | null)[]>([]);
    const uppers = useRef<(SVGPathElement | null)[]>([]);
    const creases = useRef<(SVGPathElement | null)[]>([]);
    const input = useRef({ mood, greeting, paused });
    useEffect(() => { input.current = { mood, greeting, paused }; }, [mood, greeting, paused]);

    useEffect(() => {
        const element = host.current;
        if (!element) return;
        const reduced = matchMedia("(prefers-reduced-motion: reduce)");
        const pointer = { x: 0, y: 0, inside: false, entered: 0 };
        const move = (event: PointerEvent) => {
            const box = element.getBoundingClientRect();
            pointer.x = Math.max(-1, Math.min(1, (event.clientX - box.left - box.width / 2) / (box.width / 2)));
            pointer.y = Math.max(-1, Math.min(1, (event.clientY - box.top - box.height / 2) / (box.height / 2)));
            if (!pointer.inside) pointer.entered = performance.now();
            pointer.inside = true;
        };
        const leave = () => { pointer.inside = false; };
        element.addEventListener("pointermove", move);
        element.addEventListener("pointerleave", leave);
        let visible = true;
        const observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; });
        observer.observe(element);
        let frame = 0, last = performance.now(), time = 0, helloAt = -10;
        let previousGreeting = input.current.greeting, nextBlink = 2.5, blinkAt = -10;
        let x = 0, y = 0, joy = .12, lean = 0, arm = 0, lastStatic = "";
        const life = createLife(character), duration = rig.duration * TEMPO, flourish = rig.flourish;
        const animate = (now: number) => {
            frame = requestAnimationFrame(animate);
            const dt = Math.min((now - last) / 1000, .04);
            last = now;
            if (!visible || document.hidden) return;
            const state = input.current, still = state.paused || reduced.matches;
            const signature = `${state.mood}:${state.greeting}`;
            if (still && signature === lastStatic) return;
            lastStatic = still ? signature : "";
            if (!still) time += dt;
            if (state.greeting !== previousGreeting) {
                previousGreeting = state.greeting;
                helloAt = still ? -10 : time;
            }
            const age = time - helloAt, hello = !still && age < duration;
            const progress = Math.max(0, Math.min(1, age / duration));
            const ambient = life(time, !still, hello, gain);
            const envelope = hello ? Math.sin(progress * Math.PI) : 0;
            const happy = hello || state.mood === "delighted";
            joy = damp(joy, happy ? 1 : state.mood === "idle" ? .12 : 0, still ? 100 : 6, dt);
            if (!still && time > nextBlink) { blinkAt = time; nextBlink = time + 3.7 + Math.random() * 3.2; }
            const bp = (time - blinkAt) / .19;
            const blink = !still && bp > 0 && bp < 1 ? Math.sin(bp * Math.PI) : 0;
            const thinking = state.mood === "thinking";
            // A glance is only ever a fallback: a pointer in the frame outranks
            // whatever the creature had decided to look at.
            x = damp(x, still || hello ? 0 : pointer.inside ? pointer.x * 13 : thinking ? -10 : ambient.glanceX * 12, 18, dt);
            y = damp(y, still || hello ? 0 : pointer.inside ? pointer.y * 12 : thinking ? -9 : ambient.glanceY * 9, 18, dt);
            const lower = 66 - joy * 23, top = -70 + blink * 145 + (thinking ? 12 : 0);
            for (let i = 0; i < 2; i++) {
                pupils.current[i]?.setAttribute("transform", `translate(${x} ${y})`);
                lowers.current[i]?.setAttribute("d", `M-70 80V${lower}Q0 ${lower - 14 * joy} 70 ${lower}V80Z`);
                uppers.current[i]?.setAttribute("d", `M-72 -80H72V${top}Q0 ${top + 10} -72 ${top}Z`);
                creases.current[i]?.setAttribute("opacity", String(Math.pow(blink, 5)));
            }
            const dwell = pointer.inside && now - pointer.entered > 140;
            const tilt = happy ? rig.tilt : state.mood === "listening" ? -2 : dwell ? pointer.x * 1.5 : 0;
            lean = damp(lean, still ? 0 : tilt, 4, dt);
            const bounce = flourish === "hop" ? Math.pow(Math.sin(progress * Math.PI), 2) : flourish === "spring" ? Math.abs(Math.sin(progress * Math.PI * 2)) : envelope;
            // Ambient amplitudes are in viewBox units, where 1254 spans the whole
            // stage — a couple of units is a pixel and reads as nothing at all.
            const lift = (hello ? bounce * rig.lift : 0) + ambient.hop * Math.max(rig.lift, 16) * .8 + ambient.bob * 17 + ambient.breath * 9;
            const twist = (hello && flourish === "twist" ? Math.sin(progress * Math.PI * 2) * 3 : 0) + ambient.twist * 3 + ambient.sway * 2.2;
            root.current?.setAttribute("transform", `translate(0 ${-lift}) rotate(${lean + twist} 630 1050)`);
            const spring = (hello && flourish === "spring" ? Math.sin(progress * Math.PI * 4) * .045 * envelope : 0) + ambient.squash * .05;
            const perk = hello && flourish === "perk" ? envelope * .025 : 0;
            const nod = (hello && flourish === "nod" ? Math.sin(progress * Math.PI * 2) * 6 * envelope : 0) + ambient.nod * 14;
            const shuffle = (hello && flourish === "shuffle" ? Math.sin(progress * Math.PI * 2) * 7 * envelope : 0) + ambient.slide * 14;
            // Breath narrows as it lifts, the way a chest does.
            const wide = 1 - spring / 2 + perk - ambient.breath * .008, tall = 1 + spring + perk + ambient.breath * .018;
            torso.current?.setAttribute("transform", `translate(${shuffle} ${nod}) translate(630 885) scale(${wide} ${tall}) translate(-630 -885)`);
            // Gesture begins with a lift, then a few diminishing waves, then a rest.
            const phase = Math.max(0, Math.min(1, (age - .4 * TEMPO) / ((rig.duration - 1) * TEMPO)));
            const wave = hello ? Math.sin(phase * Math.PI * rig.waves * 2) * Math.sin(phase * Math.PI) * (flourish === "nod" || flourish === "shuffle" ? 8 : 14) : 0;
            arm = damp(arm, happy ? -7 : state.mood === "waiting" ? -4 : 9, still ? 100 : 7, dt);
            left.current?.setAttribute("transform", rotate((happy ? (flourish === "perk" || flourish === "twist" ? 18 : 5) : 0) - ambient.drift * 2.2 - ambient.fidget * 5, [350, 650]));
            right.current?.setAttribute("transform", rotate(arm + wave + ambient.drift * 3 + ambient.fidget * 13, [900, 650]));
            leftFoot.current?.setAttribute("transform", rotate((hello && !("stance" in rig) ? envelope * (flourish === "step" ? 13 : 6) : 0) + ambient.step * 11, [520, 870]));
            rightFoot.current?.setAttribute("transform", rotate(hello && flourish === "hop" ? -envelope * 8 : 0, [750, 870]));
            element.dataset.pose = hello ? "greeting" : state.mood;
            element.dataset.wave = hello && phase > 0 && phase < 1 ? "waving" : "settled";
            element.dataset.gaze = pointer.inside && !still ? "following" : "resting";
        };
        frame = requestAnimationFrame(animate);
        return () => {
            cancelAnimationFrame(frame); observer.disconnect();
            element.removeEventListener("pointermove", move); element.removeEventListener("pointerleave", leave);
        };
    }, [character, rig, gain]);

    const asset = `/teammates/extended/${character}${detail}.webp`;
    const tint = [1, 3, 5].map(start => parseInt(rig.color.slice(start, start + 2), 16) / 255);
    const footColor = "foot" in rig ? rig.foot : rig.color;
    const footTint = [1, 3, 5].map(start => parseInt(footColor.slice(start, start + 2), 16) / 255);
    const legShade = (factor: number) => `rgb(${footTint.map(channel => Math.round(Math.min(1, channel * factor) * 255)).join(" ")})`;
    const limbs = [
        { key: "left", path: "M0 0H627V1254H0Z", ref: left },
        { key: "right", path: "M627 0H1254V1254H627Z", ref: right },
        { key: "left-foot", path: "M285 970H600V1220H285Z", ref: leftFoot },
        { key: "right-foot", path: "M650 970H1000V1220H650Z", ref: rightFoot },
    ];
    return <div ref={host} className={styles.scene} role="img" aria-label={`${rig.name}, ${mood}`} data-character={character}>
        <svg viewBox="0 0 1254 1254" width="100%" height="100%" aria-hidden="true">
            <defs>
                {limbs.map(limb => <clipPath key={limb.key} id={`${id}-${limb.key}`}><path d={limb.path}/></clipPath>)}
                <filter id={`${id}-tint`} colorInterpolationFilters="sRGB"><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncR type="linear" slope={tint[0] * 1.65} intercept={tint[0] * .06}/><feFuncG type="linear" slope={tint[1] * 1.65} intercept={tint[1] * .06}/><feFuncB type="linear" slope={tint[2] * 1.65} intercept={tint[2] * .06}/></feComponentTransfer></filter>
                <filter id={`${id}-feet-tint`} colorInterpolationFilters="sRGB"><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncR type="linear" slope={footTint[0] * 1.65} intercept={footTint[0] * .06}/><feFuncG type="linear" slope={footTint[1] * 1.65} intercept={footTint[1] * .06}/><feFuncB type="linear" slope={footTint[2] * 1.65} intercept={footTint[2] * .06}/></feComponentTransfer></filter>
                {["left", "right"].map(side => <linearGradient key={side} id={`${id}-${side}-leg-color`} gradientUnits="userSpaceOnUse" x1={side === "left" ? 480 : 710} x2={side === "left" ? 570 : 790}><stop stopColor={legShade(.62)}/><stop offset=".38" stopColor={legShade(1.12)}/><stop offset="1" stopColor={legShade(.76)}/></linearGradient>)}
                <radialGradient id={`${id}-white`} cx="32%" cy="24%" r="76%"><stop stopColor="#fffdf5"/><stop offset=".45" stopColor="#f5eddf"/><stop offset=".8" stopColor="#ded0bb"/><stop offset="1" stopColor="#ad8c70"/></radialGradient>
                <radialGradient id={`${id}-black`} cx="32%" cy="22%"><stop stopColor="#484441"/><stop offset=".5" stopColor="#161313"/><stop offset="1" stopColor="#080808"/></radialGradient>
                <linearGradient id={`${id}-lid`} x2=".3" y2="1"><stop stopColor="#fff9ed"/><stop offset="1" stopColor="#d9c9b1"/></linearGradient>
                <clipPath id={`${id}-eye`}><ellipse rx="56" ry="64"/></clipPath>
                <filter id={`${id}-soft`}><feGaussianBlur stdDeviation="2.1"/></filter>
                <filter id={`${id}-shadow`} x="-40%" y="-40%" width="190%" height="190%"><feDropShadow dx="0" dy="6" stdDeviation="5" floodColor="#33284a" floodOpacity=".3"/></filter>
            </defs>
            <g transform="translate(-50 -15) scale(1.08)"><g ref={root}>
                {limbs.filter(limb => limb.key.includes("foot")).map(limb => <g key={limb.key} ref={limb.ref} data-limb={limb.key}>
                    <g transform={"stance" in rig ? `translate(${limb.key === "left-foot" ? -130 : 130} 0)` : undefined}><path d={limb.key === "left-foot" ? "M535 790Q525 845 520 930" : "M715 790Q735 850 749 930"} fill="none" stroke={`url(#${id}-${limb.key === "left-foot" ? "left" : "right"}-leg-color)`} strokeWidth="77" strokeLinecap="round"/>
                    <g transform="translate(125 100) scale(.8)" filter={`url(#${id}-feet-tint)`}><image href={`/teammates/pleat-v2/base${detail}.webp`} width="1254" height="1254" clipPath={`url(#${id}-${limb.key})`}/></g></g>
                </g>)}
                <g ref={torso} data-body-motion="true">
                {limbs.filter(limb => !limb.key.includes("foot")).map(limb => <g key={limb.key} ref={limb.ref} data-limb={limb.key}>
                    <g transform={`translate(${"stance" in rig ? limb.key === "left" ? 45 : 140 : 100} ${"arm" in rig && limb.key === "right" ? -60 : 95}) scale(.8)`} filter={`url(#${id}-tint)`}><image href={`/teammates/pleat-v2/arms${detail}.webp`} width="1254" height="1254" clipPath={`url(#${id}-${limb.key})`}/></g>
                </g>)}
                <image href={asset} width="1254" height="1254" transform={`translate(${(1254 - 1254 * rig.scale) / 2} ${rig.y}) scale(${rig.scale})`}/>
                {rig.eyes.map(([ex, ey], i) => <g key={i} transform={`translate(${ex} ${ey}) scale(1.05)`}>
                    <ellipse rx="56" ry="64" fill={`url(#${id}-white)`} filter={`url(#${id}-shadow)`}/>
                    <g clipPath={`url(#${id}-eye)`}>
                        <g ref={el => { pupils.current[i] = el; }} data-pupil={i}><ellipse cy="-2" rx="37" ry="43" fill={`url(#${id}-black)`}/><ellipse cx="-9" cy="-15" rx="10" ry="11" fill="white" opacity=".68" filter={`url(#${id}-soft)`}/><ellipse cx="9" cy="12" rx="3" ry="3" fill="#a79d8e" opacity=".35"/></g>
                        <path ref={el => { lowers.current[i] = el; }} d="M-70 80V64Q0 64 70 64V80Z" fill={`url(#${id}-lid)`} stroke="#a99275" strokeOpacity=".18" strokeWidth="1.5"/>
                        <path ref={el => { uppers.current[i] = el; }} d="M-72 -80H72V-70Q0 -60 -72 -70Z" fill={`url(#${id}-lid)`}/>
                        <path ref={el => { creases.current[i] = el; }} d="M-32 5Q0 20 32 5" fill="none" stroke="#79604c" strokeWidth="3" strokeLinecap="round" opacity="0"/>
                    </g>
                </g>)}
            </g></g></g>
        </svg>
    </div>;
}
