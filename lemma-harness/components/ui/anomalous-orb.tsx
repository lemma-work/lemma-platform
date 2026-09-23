"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

import { cn } from "@/lib/utils";

type AnomalousOrbProps = Omit<React.ComponentProps<"div">, "ref">;

const vertexShader = `
uniform float time;
varying vec3 vNormal;
varying vec3 vPosition;

vec3 mod289(vec3 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 mod289(vec4 x) { return x - floor(x * (1.0 / 289.0)) * 289.0; }
vec4 permute(vec4 x) { return mod289(((x * 34.0) + 1.0) * x); }
vec4 taylorInvSqrt(vec4 r) { return 1.79284291400159 - 0.85373472095314 * r; }

float snoise(vec3 v) {
    const vec2 C = vec2(1.0 / 6.0, 1.0 / 3.0);
    const vec4 D = vec4(0.0, 0.5, 1.0, 2.0);
    vec3 i = floor(v + dot(v, C.yyy));
    vec3 x0 = v - i + dot(i, C.xxx);
    vec3 g = step(x0.yzx, x0.xyz);
    vec3 l = 1.0 - g;
    vec3 i1 = min(g.xyz, l.zxy);
    vec3 i2 = max(g.xyz, l.zxy);
    vec3 x1 = x0 - i1 + C.xxx;
    vec3 x2 = x0 - i2 + C.yyy;
    vec3 x3 = x0 - D.yyy;
    i = mod289(i);
    vec4 p = permute(permute(permute(i.z + vec4(0.0, i1.z, i2.z, 1.0)) + i.y + vec4(0.0, i1.y, i2.y, 1.0)) + i.x + vec4(0.0, i1.x, i2.x, 1.0));
    float n_ = 0.142857142857;
    vec3 ns = n_ * D.wyz - D.xzx;
    vec4 j = p - 49.0 * floor(p * ns.z * ns.z);
    vec4 x_ = floor(j * ns.z);
    vec4 y_ = floor(j - 7.0 * x_);
    vec4 x = x_ * ns.x + ns.yyyy;
    vec4 y = y_ * ns.x + ns.yyyy;
    vec4 h = 1.0 - abs(x) - abs(y);
    vec4 b0 = vec4(x.xy, y.xy);
    vec4 b1 = vec4(x.zw, y.zw);
    vec4 s0 = floor(b0) * 2.0 + 1.0;
    vec4 s1 = floor(b1) * 2.0 + 1.0;
    vec4 sh = -step(h, vec4(0.0));
    vec4 a0 = b0.xzyw + s0.xzyw * sh.xxyy;
    vec4 a1 = b1.xzyw + s1.xzyw * sh.zzww;
    vec3 p0 = vec3(a0.xy, h.x);
    vec3 p1 = vec3(a0.zw, h.y);
    vec3 p2 = vec3(a1.xy, h.z);
    vec3 p3 = vec3(a1.zw, h.w);
    vec4 norm = taylorInvSqrt(vec4(dot(p0, p0), dot(p1, p1), dot(p2, p2), dot(p3, p3)));
    p0 *= norm.x;
    p1 *= norm.y;
    p2 *= norm.z;
    p3 *= norm.w;
    vec4 m = max(0.6 - vec4(dot(x0, x0), dot(x1, x1), dot(x2, x2), dot(x3, x3)), 0.0);
    m = m * m;
    return 42.0 * dot(m * m, vec4(dot(p0, x0), dot(p1, x1), dot(p2, x2), dot(p3, x3)));
}

void main() {
    vNormal = normal;
    vPosition = position;
    float displacement = snoise(position * 2.1 + time * 0.5) * 0.18;
    vec3 newPosition = position + normal * displacement;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(newPosition, 1.0);
}
`;

const fragmentShader = `
uniform vec3 color;
uniform vec3 pointLightPos;
varying vec3 vNormal;
varying vec3 vPosition;

void main() {
    vec3 normal = normalize(vNormal);
    vec3 lightDir = normalize(pointLightPos - vPosition);
    float diffuse = max(dot(normal, lightDir), 0.0);
    float fresnel = pow(1.0 - max(dot(normal, vec3(0.0, 0.0, 1.0)), 0.0), 2.0);
    // Ambient floor raised from 0.28 so unlit facets stay legible against a
    // near-black canvas — the old value only read against a light bg.
    vec3 finalColor = color * (0.48 + diffuse * 0.5 + fresnel * 0.6);
    gl_FragColor = vec4(finalColor, 0.8);
}
`;

export function AnomalousOrb({ className, ...props }: AnomalousOrbProps) {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const container = containerRef.current;
        if (!container) return;

        const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000);
        camera.position.z = 3.4;

        const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.setClearColor(0xffffff, 0);
        // setSize below is called with updateStyle=false, so the canvas's own
        // CSS size never tracks its container unless we pin it here — without
        // this it renders at its raw buffer resolution (often larger than the
        // container after devicePixelRatio scaling) and gets cropped to one
        // corner by the container's overflow:hidden.
        renderer.domElement.style.width = "100%";
        renderer.domElement.style.height = "100%";
        renderer.domElement.style.display = "block";
        container.appendChild(renderer.domElement);

        const geometry = new THREE.IcosahedronGeometry(1.15, 48);
        const material = new THREE.ShaderMaterial({
            uniforms: {
                time: { value: 0 },
                pointLightPos: { value: new THREE.Vector3(0, 0, 4.5) },
                color: { value: new THREE.Color(0xd89b3d) },
            },
            vertexShader,
            fragmentShader,
            wireframe: true,
            transparent: true,
            depthWrite: false,
        });

        const mesh = new THREE.Mesh(geometry, material);
        scene.add(mesh);

        const resize = () => {
            const { width, height } = container.getBoundingClientRect();
            const safeWidth = Math.max(1, width);
            const safeHeight = Math.max(1, height);

            camera.aspect = safeWidth / safeHeight;
            camera.updateProjectionMatrix();
            renderer.setSize(safeWidth, safeHeight, false);
        };

        let frameId = 0;
        const animate = (time: number) => {
            material.uniforms.time.value = time * 0.00035;
            mesh.rotation.y += prefersReducedMotion ? 0 : 0.0016;
            mesh.rotation.x += prefersReducedMotion ? 0 : 0.0008;
            renderer.render(scene, camera);
            frameId = requestAnimationFrame(animate);
        };

        const resizeObserver = new ResizeObserver(resize);
        resizeObserver.observe(container);
        resize();
        renderer.render(scene, camera);
        frameId = requestAnimationFrame(animate);

        return () => {
            resizeObserver.disconnect();
            cancelAnimationFrame(frameId);
            geometry.dispose();
            material.dispose();
            renderer.dispose();
            renderer.domElement.remove();
        };
    }, []);

    return (
        <div
            ref={containerRef}
            className={cn("pointer-events-none absolute overflow-hidden", className)}
            aria-hidden="true"
            {...props}
        />
    );
}
