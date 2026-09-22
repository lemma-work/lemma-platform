// AnomalousOrb, ported from lemma-frontend/components/ui/anomalous-orb.tsx.
// Same shaders, geometry, and gold (0x7a5ce0); three.js vendored locally so
// the splash never depends on the network.
//
// Imported where it is used rather than at the top of the module. It is
// 356 KB of JavaScript to parse before anything else in this file runs, on
// the one screen whose whole job is to appear immediately -- and it was
// being parsed even when it could not be used: the reduced-motion check
// below is what decides whether an orb is drawn at all, and a static import
// is fetched and evaluated regardless of what any check afterwards says.
// Someone who has told their computer they do not want motion paid the whole
// cost for nothing.
//
// Every caller reaches the orb through `window.__orb?.`, so arriving late is
// already the case they handle.

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
  vec3 finalColor = color * (0.28 + diffuse * 0.58 + fresnel * 0.68);
  gl_FragColor = vec4(finalColor, 0.46);
}
`;

const container = document.getElementById("orb");
const prefersReducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
// Freezing shader time still redraws every frame, including on software GPUs.
// Reduced motion must avoid the rendering loop and its graphics context.
if (!prefersReducedMotion) {
  try {
    const THREE = await import("./vendor/three.module.min.js");
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 1000);
    camera.position.z = 3.4;

    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setClearColor(0xffffff, 0);
    container.appendChild(renderer.domElement);

    const geometry = new THREE.IcosahedronGeometry(1.15, 8);
    const material = new THREE.ShaderMaterial({
      uniforms: {
        time: { value: 0 },
        pointLightPos: { value: new THREE.Vector3(0, 0, 4.5) },
        color: { value: new THREE.Color(0x7a5ce0) },
      },
      vertexShader,
      fragmentShader,
      wireframe: true,
      transparent: true,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(geometry, material);
    scene.add(mesh);

    let speed = 1;
    const targetColor = new THREE.Color(0x7a5ce0);
    window.__orb = {
      awake() { speed = 2.4; targetColor.set(0xeab451); setTimeout(() => { speed = 1.3; }, 1800); },
      stall() { speed = 0.12; targetColor.set(0xb0a48d); },
      resume() { speed = 1; targetColor.set(0x7a5ce0); },
    };

    const resize = () => {
      const { width, height } = container.getBoundingClientRect();
      camera.aspect = Math.max(1, width) / Math.max(1, height);
      camera.updateProjectionMatrix();
      renderer.setSize(Math.max(1, width), Math.max(1, height), false);
    };
    new ResizeObserver(resize).observe(container);
    resize();

    let clock = 0;
    let last = performance.now();
    const animate = (now) => {
      const dt = now - last;
      last = now;
      clock += dt * speed;
      material.uniforms.time.value = clock * 0.00035;
      material.uniforms.color.value.lerp(targetColor, 0.04);
      mesh.rotation.y += 0.0016 * speed;
      mesh.rotation.x += 0.0008 * speed;
      if (!document.hidden && container.getClientRects().length) renderer.render(scene, camera);
      requestAnimationFrame(animate);
    };
    requestAnimationFrame(animate);
  } catch {
    // Decorative graphics must never prevent setup on machines without WebGL.
    container.hidden = true;
  }
}
