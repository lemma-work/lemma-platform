"use client";

import { useEffect, useId, useRef } from "react";
import type { Mood } from "./loop/loop-puppet";
import { createLife, lifeGain, SMALL_ABOVE, TEMPO } from "./life";
import styles from "./loop/page.module.css";

export type CastName = "pleat" | "frame";
type Point = readonly [number, number];
const RIGS = {
    pleat: {
        name: "Pleat", eyes: [[526, 357], [730, 357]], eyeScale: 1.42,
        left: "M0 0H627V1254H0Z", right: "M627 0H1254V1254H627Z",
        leftFoot: "M300 922H597V1220H300Z", rightFoot: "M645 922H960V1220H645Z",
        shoulders: [[284, 668], [975, 678]], hips: [[496, 945], [750, 945]],
        waving: "right", duration: 4.1, waves: 2, waveSize: 10, lift: 5,
    },
    frame: {
        name: "Frame", eyes: [[550, 355], [741, 355]], eyeScale: 1.48,
        left: "M0 0H627V1254H0Z", right: "M627 0H1254V1254H627Z",
        leftFoot: "M285 942H600V1220H285Z", rightFoot: "M650 942H1000V1220H650Z",
        shoulders: [[304, 672], [995, 677]], hips: [[508, 961], [762, 961]],
        waving: "left", duration: 3.5, waves: 3, waveSize: 14, lift: 9,
    },
} as const;

const damp = (a: number, b: number, rate: number, dt: number) => a + (b - a) * (1 - Math.exp(-rate * dt));
const rotate = (angle: number, point: Point) => `rotate(${angle} ${point[0]} ${point[1]})`;

/** How big this is actually drawn, in CSS pixels. It decides two things:
 *  how far the ambient motion has to be exaggerated to stay visible, and
 *  which copy of the art to fetch — a 1254-square render is wasted on a
 *  rail mark, and twenty-one of them is what made the gallery cost 19MB. */
export function CastPuppet({ character, mood, greeting, paused, pixels = 620 }: { character: CastName; mood: Mood; greeting: number; paused: boolean; pixels?: number }) {
    const gain = lifeGain(pixels), detail = pixels <= SMALL_ABOVE ? "-sm" : "";
    const rig = RIGS[character];
    const id = useId().replaceAll(":", "");
    const host = useRef<HTMLDivElement>(null);
    const root = useRef<SVGGElement>(null);
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
        let x = 0, y = 0, joy = .12, lean = 0, arm = 0;
        const life = createLife(character), duration = rig.duration * TEMPO;
        const animate = (now: number) => {
            frame = requestAnimationFrame(animate);
            const dt = Math.min((now - last) / 1000, .04);
            last = now;
            if (!visible || document.hidden) return;
            const state = input.current, still = state.paused || reduced.matches;
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
            const tilt = happy ? (character === "pleat" ? -1.5 : 2.2) : state.mood === "listening" ? -2 : dwell ? pointer.x * 1.5 : 0;
            lean = damp(lean, still ? 0 : tilt, 4, dt);
            // These two have no torso group of their own, so the breath is a
            // whole-body stretch anchored at the boots — which is what a breath
            // looks like on something with no neck anyway. Amplitudes are in
            // viewBox units, where 1254 spans the stage.
            const lift = envelope * rig.lift + ambient.hop * 16 + ambient.bob * 17 + ambient.breath * 9;
            const wide = 1 - ambient.squash * .025 - ambient.breath * .008, tall = 1 + ambient.squash * .05 + ambient.breath * .018;
            root.current?.setAttribute("transform", `translate(${ambient.slide * 14} ${-lift}) rotate(${lean + ambient.sway * 2.2 + ambient.twist * 3} 630 1150) translate(630 1150) scale(${wide} ${tall}) translate(-630 -1150)`);
            // Gesture begins with a lift, then a few diminishing waves, then a rest.
            const phase = Math.max(0, Math.min(1, (age - .4 * TEMPO) / ((rig.duration - 1) * TEMPO)));
            const wave = hello ? Math.sin(phase * Math.PI * rig.waves * 2) * Math.sin(phase * Math.PI) * rig.waveSize : 0;
            arm = damp(arm, happy ? -7 : state.mood === "waiting" ? -4 : 9, still ? 100 : 7, dt);
            const isPleat = character === "pleat";
            // The waving arm carries the fidget; the other one only drifts, so a
            // fidget reads as one hand moving rather than a shrug.
            const busy = ambient.drift * 3 + ambient.fidget * 13, calm = -ambient.drift * 2.2 - ambient.fidget * 5;
            left.current?.setAttribute("transform", rotate((isPleat ? joy * 5 : -arm + wave) + (isPleat ? calm : busy), rig.shoulders[0]));
            right.current?.setAttribute("transform", rotate((isPleat ? arm + wave : -joy * 6) + (isPleat ? busy : calm), rig.shoulders[1]));
            // One boot remains planted; the other turns from its hip for a small toe lift.
            leftFoot.current?.setAttribute("transform", rotate((isPleat ? 0 : envelope * 6) + ambient.step * 11, rig.hips[0]));
            rightFoot.current?.setAttribute("transform", rotate(isPleat ? -envelope * 4 : 0, rig.hips[1]));
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

    const asset = `/teammates/${character}-v2`;
    const limbs = [
        { key: "left", path: rig.left, ref: left }, { key: "right", path: rig.right, ref: right },
        { key: "left-foot", path: rig.leftFoot, ref: leftFoot }, { key: "right-foot", path: rig.rightFoot, ref: rightFoot },
    ];
    return <div ref={host} className={styles.scene} role="img" aria-label={`${rig.name}, ${mood}`} data-character={character}>
        <svg viewBox="0 0 1254 1254" width="100%" height="100%" aria-hidden="true">
            <defs>
                {limbs.map(limb => <clipPath key={limb.key} id={`${id}-${limb.key}`}><path d={limb.path}/></clipPath>)}
                <radialGradient id={`${id}-white`} cx="32%" cy="24%" r="76%"><stop stopColor="#fffdf5"/><stop offset=".45" stopColor="#f5eddf"/><stop offset=".8" stopColor="#ded0bb"/><stop offset="1" stopColor="#ad8c70"/></radialGradient>
                <radialGradient id={`${id}-black`} cx="32%" cy="22%"><stop stopColor="#484441"/><stop offset=".5" stopColor="#161313"/><stop offset="1" stopColor="#080808"/></radialGradient>
                <linearGradient id={`${id}-lid`} x2=".3" y2="1"><stop stopColor="#fff9ed"/><stop offset="1" stopColor="#d9c9b1"/></linearGradient>
                <clipPath id={`${id}-eye`}><ellipse rx="56" ry="64"/></clipPath>
                <filter id={`${id}-soft`}><feGaussianBlur stdDeviation="2.1"/></filter>
                <filter id={`${id}-shadow`} x="-40%" y="-40%" width="190%" height="190%"><feDropShadow dx="0" dy="6" stdDeviation="5" floodColor={character === "pleat" ? "#4a236e" : "#092e74"} floodOpacity=".3"/></filter>
            </defs>
            <g transform="translate(63 63) scale(.9)"><g ref={root}>
                {limbs.map(limb => <g key={limb.key} ref={limb.ref} data-limb={limb.key}><image href={`${asset}/${limb.key.includes("foot") ? "base" : "arms"}${detail}.webp`} width="1254" height="1254" clipPath={`url(#${id}-${limb.key})`}/></g>)}
                <image href={`${asset}/body${detail}.webp`} width="1254" height="1254"/>
                {rig.eyes.map(([ex, ey], i) => <g key={i} transform={`translate(${ex} ${ey}) scale(${rig.eyeScale})`}>
                    <ellipse rx="56" ry="64" fill={`url(#${id}-white)`} filter={`url(#${id}-shadow)`}/>
                    <g clipPath={`url(#${id}-eye)`}>
                        <g ref={el => { pupils.current[i] = el; }} data-pupil={i}><ellipse cy="-2" rx="37" ry="43" fill={`url(#${id}-black)`}/><ellipse cx="-9" cy="-15" rx="10" ry="11" fill="white" opacity=".68" filter={`url(#${id}-soft)`}/><ellipse cx="9" cy="12" rx="3" ry="3" fill="#a79d8e" opacity=".35"/></g>
                        <path ref={el => { lowers.current[i] = el; }} d="M-70 80V64Q0 64 70 64V80Z" fill={`url(#${id}-lid)`} stroke="#a99275" strokeOpacity=".18" strokeWidth="1.5"/>
                        <path ref={el => { uppers.current[i] = el; }} d="M-72 -80H72V-70Q0 -60 -72 -70Z" fill={`url(#${id}-lid)`}/>
                        <path ref={el => { creases.current[i] = el; }} d="M-32 5Q0 20 32 5" fill="none" stroke="#79604c" strokeWidth="3" strokeLinecap="round" opacity="0"/>
                    </g>
                </g>)}
            </g></g>
        </svg>
    </div>;
}
