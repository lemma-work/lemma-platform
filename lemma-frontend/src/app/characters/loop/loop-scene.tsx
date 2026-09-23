"use client";

import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import styles from "./page.module.css";

export type Mood = "idle" | "listening" | "thinking" | "waiting";
export function LoopScene({ mood, greeting, paused }: { mood: Mood; greeting: number; paused: boolean }) {
    const host = useRef<HTMLDivElement>(null);
    const inputs = useRef({ mood, greeting, paused });
    const [failed, setFailed] = useState(false);
    useEffect(() => { inputs.current = { mood, greeting, paused }; }, [mood, greeting, paused]);
    useEffect(() => {
        if (!host.current) return;
        const element: HTMLDivElement = host.current;
        let renderer: THREE.WebGLRenderer;
        try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true }); }
        catch { setFailed(true); return; }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setClearColor(0x000000, 0);
        renderer.toneMapping = THREE.ACESFilmicToneMapping;
        renderer.toneMappingExposure = .9;
        element.appendChild(renderer.domElement);
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(32, 1, .1, 50);
        camera.position.set(0, .35, 9.6); camera.lookAt(0, .1, 0);
        const pmrem = new THREE.PMREMGenerator(renderer);
        const room = new RoomEnvironment();
        const environment = pmrem.fromScene(room, .04);
        scene.environment = environment.texture;
        scene.environmentIntensity = .55;
        room.dispose(); pmrem.dispose();
        scene.add(new THREE.HemisphereLight(0xfff6e5, 0x604573, .7));
        const key = new THREE.DirectionalLight(0xffecd9, 2); key.position.set(-3, 5, 6); scene.add(key);
        const rim = new THREE.DirectionalLight(0xb1bbff, 1.5); rim.position.set(4, 2, -3); scene.add(rim);
        const orange = new THREE.MeshPhysicalMaterial({ color: 0xff713e, roughness: .32, clearcoat: .3, clearcoatRoughness: .4 });
        const ivory = new THREE.MeshPhysicalMaterial({ color: 0xfff7e6, roughness: .24, clearcoat: .4 });
        const black = new THREE.MeshPhysicalMaterial({ color: 0x131017, roughness: .16, clearcoat: 1 });
        const gleam = new THREE.MeshBasicMaterial({ color: 0xffffff });
        const sphere = new THREE.SphereGeometry(1, 40, 28);
        const body = new THREE.Group(); scene.add(body);
        class Ribbon extends THREE.Curve<THREE.Vector3> {
            constructor() { super(); }
            getPoint(t: number) { const a = t * Math.PI * 2; return new THREE.Vector3(.78 * Math.sin(2 * a), 1.27 * Math.cos(a), .29 * Math.sin(a)); }
        }
        const ribbonGeo = new THREE.TubeGeometry(new Ribbon(), 160, .29, 24, true);
        const colors = []; const pos = ribbonGeo.attributes.position;
        const coral = new THREE.Color(0xff743c); const purple = new THREE.Color(0x7840bf);
        for (let i = 0; i < pos.count; i++) {
            const center = new Ribbon().getPoint(Math.floor(i / 25) / 160);
            const inward = -((pos.getX(i) - center.x) * center.x + (pos.getY(i) - center.y) * (center.y - Math.sign(center.y) * .68));
            const mix = THREE.MathUtils.smoothstep(inward, .035, .14);
            const c = coral.clone().lerp(purple, mix); colors.push(c.r, c.g, c.b);
        }
        ribbonGeo.setAttribute("color", new THREE.Float32BufferAttribute(colors, 3));
        const ribbonMat = new THREE.MeshPhysicalMaterial({ vertexColors: true, roughness: .3, clearcoat: .4 });
        const ribbon = new THREE.Mesh(ribbonGeo, ribbonMat); body.add(ribbon);
        const ellipsoid = (parent: THREE.Object3D, material: THREE.Material, x: number, y: number, z: number, sx: number, sy: number, sz: number) => {
            const mesh = new THREE.Mesh(sphere, material); mesh.position.set(x, y, z); mesh.scale.set(sx, sy, sz); parent.add(mesh); return mesh;
        };
        // Feet remain planted while the ribbon and face articulate above them.
        for (const side of [-1, 1]) {
            ellipsoid(scene, orange, side * .46, -1.72, .07, .32, .18, .43);
            ellipsoid(scene, orange, side * .43, -1.48, -.03, .14, .29, .16);
        }
        const arms: THREE.Group[] = [];
        for (const side of [-1, 1]) {
            const arm = new THREE.Group(); arm.position.set(side * .78, -.4, .02); body.add(arm);
            const upper = ellipsoid(arm, orange, side * .14, -.2, 0, .14, .35, .15); upper.rotation.z = side * .5;
            ellipsoid(arm, orange, side * .27, -.48, .08, .21, .23, .18);
            ellipsoid(arm, orange, side * .1, -.43, .18, .1, .14, .1);
            arms.push(arm);
        }
        const face = new THREE.Group(); face.position.set(0, .06, .43); face.rotation.z = -.13; body.add(face);
        const eyes: {group: THREE.Group; pupil: THREE.Group; lid: THREE.Mesh}[] = [];
        const lidGeometry = new THREE.SphereGeometry(1, 40, 24, 0, Math.PI * 2, 0, Math.PI / 2);
        for (const side of [-1, 1]) {
            const group = new THREE.Group(); group.position.set(side * .305, 0, 0); face.add(group);
            ellipsoid(group, ivory, 0, 0, 0, .315, .365, .255);
            const pupil = new THREE.Group(); group.add(pupil);
            ellipsoid(pupil, black, 0, 0, .228, .135, .16, .065);
            ellipsoid(pupil, gleam, -.04, .062, .283, .035, .041, .015);
            const lid = new THREE.Mesh(lidGeometry, orange); lid.scale.set(.322, .373, .267); lid.rotation.x = -Math.PI / 2; group.add(lid);
            eyes.push({group, pupil, lid});
        }
        const target = { x: 0, y: 0, inside: false, since: 0 };
        const pointer = (event: PointerEvent) => {
            const rect = element.getBoundingClientRect();
            target.x = THREE.MathUtils.clamp(((event.clientX - rect.left) / rect.width - .5) * 2, -1, 1);
            target.y = THREE.MathUtils.clamp(-((event.clientY - rect.top) / rect.height - .5) * 2, -1, 1);
            if (!target.inside) target.since = performance.now(); target.inside = true;
        };
        const leave = () => { target.inside = false; target.x = 0; target.y = 0; };
        element.addEventListener("pointermove", pointer); element.addEventListener("pointerleave", leave);
        const reduced = matchMedia("(prefers-reduced-motion: reduce)");
        let visible = true;
        const observer = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; }); observer.observe(element);
        const resize = new ResizeObserver(() => { const {width, height} = element.getBoundingClientRect(); renderer.setSize(width, height); camera.aspect = width / height; camera.updateProjectionMatrix(); }); resize.observe(element);
        let raf = 0, last = performance.now(), elapsed = 0, nextBlink = 2.8, blinkAt = -100;
        let previousMood: Mood = "idle", previousGreeting = 0, nodAt = -100, stateAt = 0, gazeX = 0, gazeY = 0;
        const damp = (a: number, b: number, speed: number, dt: number) => THREE.MathUtils.damp(a, b, speed, dt);
        function frame(now: number) {
            raf = requestAnimationFrame(frame);
            const dt = Math.min((now - last) / 1000, .05); last = now;
            if (!visible || document.hidden) return;
            const input = inputs.current;
            const still = reduced.matches || input.paused;
            if (!still) elapsed += dt;
            if (input.mood !== previousMood) { stateAt = elapsed; previousMood = input.mood; blinkAt = elapsed; }
            if (input.greeting !== previousGreeting) { previousGreeting = input.greeting; nodAt = elapsed; blinkAt = elapsed + .2; }
            if (!still && elapsed > nextBlink) { blinkAt = elapsed; nextBlink = elapsed + 3.2 + Math.random() * 4.1; }
            const blinkPhase = (elapsed - blinkAt) / .18;
            const blink = !still && blinkPhase > 0 && blinkPhase < 1 ? Math.sin(blinkPhase * Math.PI) : 0;
            const age = elapsed - stateAt;
            const thinking = input.mood === "thinking";
            const listening = input.mood === "listening";
            const waiting = input.mood === "waiting";
            // Eyes acquire attention quickly. Body follows after a short dwell.
            let gx = target.inside ? target.x * .095 : 0;
            let gy = target.inside ? target.y * .085 : 0;
            if (!target.inside && thinking) { gx = -.075; gy = .045; }
            if (!target.inside && listening) { gx = .018; gy = .015; }
            if (elapsed - nodAt < 1.1) { gx = 0; gy = 0; }
            gazeX = damp(gazeX, still ? 0 : gx, 17, dt); gazeY = damp(gazeY, still ? 0 : gy, 17, dt);
            const attentive = target.inside && now - target.since > 140;
            const nodAge = elapsed - nodAt;
            const nod = !still && nodAge > 0 && nodAge < .85 ? Math.sin(nodAge / .85 * Math.PI * 2) * Math.sin(nodAge / .85 * Math.PI) * .12 : 0;
            body.rotation.y = damp(body.rotation.y, still ? 0 : attentive ? target.x * .13 : thinking ? -.1 : 0, 4, dt);
            body.rotation.z = damp(body.rotation.z, still ? 0 : listening ? -.065 : attentive ? -target.x * .035 : 0, 3, dt);
            body.rotation.x = damp(body.rotation.x, still ? 0 : nod + (listening ? .045 : 0), 10, dt);
            body.scale.y = damp(body.scale.y, still ? 1 : 1 + Math.sin(elapsed * 1.1) * .004 + nod * .12, 5, dt);
            body.position.y = (body.scale.y - 1) * 1.25;
            face.rotation.z = damp(face.rotation.z, listening && !still ? -.19 : -.13, 4, dt);
            eyes.forEach((eye, i) => {
                eye.pupil.position.set(gazeX, gazeY, 0);
                const relaxed = thinking ? .32 : listening ? .04 : .09;
                eye.lid.rotation.x = -Math.PI / 2 + (relaxed + blink * (1 - relaxed)) * Math.PI;
                eye.group.position.y = i === 1 && listening ? .025 : 0;
            });
            // A single lift into a held pose. Never an endless attention wave.
            arms[1].rotation.z = damp(arms[1].rotation.z, waiting ? 2.35 : 0, 5, dt);
            arms[1].rotation.x = damp(arms[1].rotation.x, waiting ? -.35 : 0, 5, dt);
            arms[0].rotation.z = damp(arms[0].rotation.z, thinking ? -.35 : 0, 5, dt);
            element.dataset.pose = input.mood;
            element.dataset.gaze = target.inside && !still ? "following" : "resting";
            element.dataset.phase = nodAge < 1 && !still ? "acknowledging" : age < .6 ? "settling" : "settled";
            renderer.render(scene, camera);
        }
        raf = requestAnimationFrame(frame);
        return () => {
            cancelAnimationFrame(raf); observer.disconnect(); resize.disconnect();
            element.removeEventListener("pointermove", pointer); element.removeEventListener("pointerleave", leave);
            const geometries = new Set<THREE.BufferGeometry>(); const materials = new Set<THREE.Material>();
            scene.traverse(object => { if (object instanceof THREE.Mesh) { geometries.add(object.geometry); const mats = Array.isArray(object.material) ? object.material : [object.material]; mats.forEach(m => materials.add(m)); } });
            geometries.forEach(g => g.dispose()); materials.forEach(m => m.dispose()); environment.dispose(); renderer.dispose(); renderer.domElement.remove();
        };
    }, []);
    return <div className={styles.scene} ref={host} role="img" aria-label={`Loop, ${mood === "idle" ? "at ease" : mood}`}>
        {failed && <div className={styles.fallback}><img src="/teammates/loop-v1.png" alt="Loop" /><p>3D isn’t available in this browser. Showing the still portrait.</p></div>}
    </div>;
}
