"use client";

import { useEffect, useRef } from "react";
import s from "./landing.module.css";

/** The marketing pages' world: white, with violet light coming from behind
 *  each real screen of the product, so the platform reads as the heavy,
 *  lit thing on the page and everything else as paper around it.
 *
 *  Anything marked `data-lamp` has light behind it, wide and soft and
 *  strongest just under its middle, dithered at its edge and drifting a
 *  little. Anything marked `data-quiet` sits on plain white: words never
 *  sit on the grain. Drawn at a third of the screen's size and scaled up
 *  unsmoothed, so the dither reads as pixels and the shader stays cheap.
 *  With reduced motion it draws one still frame per scroll; without WebGL
 *  the page is plain white. */

const PIXEL = 3;
const LAMPS = 4;
const QUIET = 12;

const VERTEX = "attribute vec2 p; void main(){ gl_Position = vec4(p, 0.0, 1.0); }";

const FRAGMENT = `
precision highp float;
uniform vec2 u_res; uniform float u_time; uniform float u_px; uniform vec2 u_edge;
uniform vec4 u_lamp[${LAMPS}]; uniform float u_power[${LAMPS}]; uniform vec4 u_quiet[${QUIET}];
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float noise(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f);
  return mix(mix(hash(i), hash(i+vec2(1,0)), f.x), mix(hash(i+vec2(0,1)), hash(i+vec2(1,1)), f.x), f.y); }
float fbm(vec2 p){ float v = 0.0, a = 0.5; for (int i = 0; i < 5; i++){ v += a*noise(p); p *= 2.03; a *= 0.5; } return v; }
float bayer2(vec2 a){ a = floor(a); return fract(a.x/2.0 + a.y*a.y*0.75); }
float bayer4(vec2 a){ return bayer2(0.5*a)*0.25 + bayer2(a); }
float bayer8(vec2 a){ return bayer4(0.5*a)*0.25 + bayer2(a); }
float outside(vec2 p, vec4 r){ vec2 h = r.zw * 0.5; vec2 d = abs(p - r.xy - h) - h; return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0); }
vec3 ramp(float i){
  if (i < 0.5) return vec3(1.0);
  if (i < 1.5) return vec3(0.965, 0.953, 1.0);
  if (i < 2.5) return vec3(0.914, 0.890, 1.0);
  if (i < 3.5) return vec3(0.827, 0.784, 1.0);
  if (i < 4.5) return vec3(0.690, 0.620, 0.988);
  return vec3(0.537, 0.443, 0.949);
}
float lamp(vec2 p, vec4 f){
  if (f.z <= 0.0) return 0.0;
  vec2 c = vec2(f.x + f.z * 0.5, f.y + f.w * 0.62);
  vec2 d = vec2((p.x - c.x) / (f.z * 0.78), (p.y - c.y) / (f.w * 0.78));
  return exp(-dot(d, d) * 1.25);
}
void main(){
  vec2 css = vec2(gl_FragCoord.x, u_res.y - gl_FragCoord.y) * u_px;
  vec2 uv = gl_FragCoord.xy / u_res;
  float aspect = u_res.x / u_res.y;
  vec2 q = vec2(uv.x * aspect * 1.6 + u_time * 0.010, uv.y * 3.2 - u_time * 0.004);
  float c = fbm(q + fbm(q * 0.6 + u_time * 0.004) * 0.8);
  float light = 0.0;
  for (int k = 0; k < ${LAMPS}; k++) light = max(light, lamp(css, u_lamp[k]) * u_power[k]);
  float t = light * (1.0 + 0.6 * (c - 0.4));
  float quiet = 0.0;
  for (int k = 0; k < ${QUIET}; k++) quiet = max(quiet, 1.0 - smoothstep(u_edge.x, u_edge.x + u_edge.y, outside(css, u_quiet[k])));
  t *= 1.0 - quiet * 0.92;
  float x = clamp(t, 0.0, 1.0) * 5.0;
  float i = floor(x), f = fract(x);
  vec3 grain = ramp(f > bayer8(gl_FragCoord.xy) ? i + 1.0 : i);
  vec3 still = mix(ramp(i), ramp(i + 1.0), f);
  gl_FragColor = vec4(mix(grain, still, smoothstep(0.2, 0.85, quiet)), 1.0);
}`;

/* How bright a lamp is: the landing's big screens at full light, a product
   page's smaller pictures softer, so their pools stay light and do not pile
   up into violet. */
const POWER: Record<string, number> = { "": 1, soft: 0.62 };

/** The boxes of the marked elements on or near the screen, as many as fit,
 *  and for lamps how bright each one is. */
function boxes(selector: string, limit: number, margin: number): { box: Float32Array; power: Float32Array } {
    const box = new Float32Array(limit * 4).fill(0);
    const power = new Float32Array(limit).fill(0);
    let n = 0;
    for (const node of document.querySelectorAll<HTMLElement>(selector)) {
        const rect = node.getBoundingClientRect();
        if (rect.bottom < -margin || rect.top > window.innerHeight + margin || rect.width === 0 || n === limit) continue;
        box.set([rect.left, rect.top, rect.width, rect.height], n * 4);
        power[n] = POWER[node.dataset.lamp ?? ""] ?? 1;
        n += 1;
    }
    return { box, power };
}

export function Daylight() {
    const canvas = useRef<HTMLCanvasElement>(null);

    useEffect(() => {
        const node = canvas.current;
        const gl = node?.getContext("webgl", { antialias: false, premultipliedAlpha: false });
        if (!node || !gl) return;
        const compile = (type: number, source: string) => { const shader = gl.createShader(type)!; gl.shaderSource(shader, source); gl.compileShader(shader); return shader; };
        const program = gl.createProgram()!;
        gl.attachShader(program, compile(gl.VERTEX_SHADER, VERTEX));
        gl.attachShader(program, compile(gl.FRAGMENT_SHADER, FRAGMENT));
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
        gl.useProgram(program);
        gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
        const at = gl.getAttribLocation(program, "p");
        gl.enableVertexAttribArray(at);
        gl.vertexAttribPointer(at, 2, gl.FLOAT, false, 0, 0);
        const u = (name: string) => gl.getUniformLocation(program, name);
        const uniform = { res: u("u_res"), time: u("u_time"), px: u("u_px"), edge: u("u_edge"), lamp: u("u_lamp"), power: u("u_power"), quiet: u("u_quiet") };
        node.dataset.ready = "";

        const calm = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const start = performance.now();
        let frame = 0;
        const fit = () => {
            node.width = Math.ceil(window.innerWidth / PIXEL);
            node.height = Math.ceil(window.innerHeight / PIXEL);
            gl.viewport(0, 0, node.width, node.height);
        };
        const draw = (now: number) => {
            const phone = window.innerWidth < 900;
            gl.uniform2f(uniform.res, node.width, node.height);
            gl.uniform1f(uniform.time, calm ? 40 : (now - start) / 1000);
            gl.uniform1f(uniform.px, PIXEL);
            gl.uniform2f(uniform.edge, phone ? 20 : 28, phone ? 60 : 110);
            const lamps = boxes("[data-lamp]", LAMPS, 400);
            gl.uniform4fv(uniform.lamp, lamps.box);
            gl.uniform1fv(uniform.power, lamps.power);
            gl.uniform4fv(uniform.quiet, boxes("[data-quiet]", QUIET, 100).box);
            gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
        };
        const loop = (now: number) => { draw(now); frame = requestAnimationFrame(loop); };
        const once = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(draw); };

        fit();
        window.addEventListener("resize", fit);
        if (calm) {
            once();
            window.addEventListener("scroll", once, { passive: true });
            window.addEventListener("resize", once);
        } else {
            frame = requestAnimationFrame(loop);
        }
        return () => {
            cancelAnimationFrame(frame);
            window.removeEventListener("resize", fit);
            window.removeEventListener("scroll", once);
            window.removeEventListener("resize", once);
        };
    }, []);

    return <canvas ref={canvas} className={s.sky} aria-hidden="true" />;
}
