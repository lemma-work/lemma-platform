import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { ContactShadows, OrbitControls, OrthographicCamera, Text, useGLTF } from "@react-three/drei";
import {
  AtSign,
  Building2,
  Castle,
  Check,
  CheckCircle2,
  ChevronLeft,
  Circle,
  ClipboardList,
  Clock,
  Crown,
  Droplets,
  Film,
  Gem,
  Hammer,
  Hash,
  Layers,
  Play,
  Plus,
  Send,
  Ship,
  Sparkles,
  Store,
  Trees,
  Trophy,
  Users,
  Wind,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { useAuth, useFunctionRun, useRecords } from "lemma-sdk/react";
import VisualProductDoc from "./VisualProductDoc";
import {
  client,
  gravatarUrl,
  type TaskRow,
  type SprintRow,
  type TeamMemberRow,
  type CatalogueItemRow,
  type TaskStatus as TaskStatusType,
  type MemberRole,
} from "./lemma";
import { CinematicDirector, type CineControls, type CineScript } from "./cinematic";
import { QuartermasterPage } from "./QuartermasterPage";
import { DEMOS } from "./demos";

// ---------------------------------------------------------------------------
// MODEL
// ---------------------------------------------------------------------------

type Tier = 15 | 30 | 45 | 60;
type ComponentKind =
  | "sapling" | "mushrooms" | "crystals" | "lantern"
  | "tree" | "stall" | "fountain" | "cart"
  | "cottage" | "watermill" | "taco_stand" | "watchtower"
  | "ship" | "castle_gate" | "windmill" | "manor" | "grand_fountain";
type PlacementState = "under_review" | "established" | "demolished";
type AppTab = "world" | "tasks" | "review" | "all" | "catalog" | "kits" | "stats" | "roadmap" | "demos" | "quartermaster";
type TaskSource = "slack" | "email" | "telegram";
type TaskStatus = "assigned" | "cleared" | "under_review" | "established" | "demolished";

type Member = { name: string; email: string; color: string; points: number };
type CatalogueItem = { kind: ComponentKind; label: string; tier: Tier };
type Placement = {
  id: string;
  task: string;
  builder: string;
  builderEmail: string;
  item: CatalogueItem;
  x: number;
  z: number;
  state: PlacementState;
  submitted: string;
};
type Role = "manager" | "member" | "viewer";
type MockTask = {
  id: string;
  title: string;
  assignee: string;
  assigner: string;
  tier: Tier;
  source: TaskSource;
  sprintId: string;
  status: TaskStatus;
  due: string;
  component?: string | null;
  worldX?: number | null;
  worldZ?: number | null;
};

const TIER_COLOR: Record<Tier, string> = { 15: "#7fc26b", 30: "#3fa3df", 45: "#f0a92e", 60: "#8e6fd6" };
const TIER_LABEL: Record<Tier, string> = { 15: "Quick win", 30: "Half-day", 45: "Deliverable", 60: "Milestone" };

const SOURCE_CFG: Record<TaskSource, { label: string; bg: string; icon: React.ReactNode }> = {
  slack:    { label: "Slack",    bg: "#4a154b", icon: <Hash size={10} /> },
  email:    { label: "Email",    bg: "#3fa3df", icon: <AtSign size={10} /> },
  telegram: { label: "Telegram", bg: "#2aabee", icon: <Send size={10} /> },
};

const CATALOGUE: CatalogueItem[] = [
  { kind: "sapling",     label: "Sapling",        tier: 15 },
  { kind: "mushrooms",   label: "Mushroom patch",  tier: 15 },
  { kind: "crystals",    label: "Crystal rocks",   tier: 15 },
  { kind: "lantern",     label: "Lantern",         tier: 15 },
  { kind: "tree",        label: "Canopy tree",     tier: 30 },
  { kind: "stall",       label: "Market stall",    tier: 30 },
  { kind: "fountain",    label: "Fountain",        tier: 30 },
  { kind: "cart",        label: "Cart",            tier: 30 },
  { kind: "cottage",     label: "Cottage",         tier: 45 },
  { kind: "watermill",   label: "Watermill",       tier: 45 },
  { kind: "taco_stand",  label: "Taco stand",      tier: 45 },
  { kind: "watchtower",  label: "Watchtower",      tier: 45 },
  { kind: "ship",        label: "Large ship",      tier: 60 },
  { kind: "castle_gate", label: "Castle gate",     tier: 60 },
  { kind: "windmill",    label: "Castle tower",    tier: 60 },
  { kind: "manor",       label: "Manor",           tier: 60 },
  { kind: "grand_fountain", label: "Grand fountain", tier: 60 },
];

const COMPONENT_BY_KIND: Record<string, CatalogueItem> = Object.fromEntries(CATALOGUE.map((c) => [c.kind, c]));
const itemForKind = (kind: string): CatalogueItem => COMPONENT_BY_KIND[kind] ?? CATALOGUE.find((c) => c.kind === "tree")!;

const ROSTER_FALLBACK: { name: string; email: string; color: string; role: Role }[] = [
  { name: "Manager", email: "manager@example.com",  color: "#2f8d4d", role: "manager" },
  { name: "Asha",  email: "asha@example.com",  color: "#42be65", role: "member"  },
  { name: "Rohan", email: "rohan@example.com", color: "#4f90df", role: "member"  },
  { name: "Maya",  email: "maya@example.com",  color: "#a878e4", role: "member"  },
  { name: "Kabir", email: "kabir@example.com", color: "#efad32", role: "member"  },
  { name: "Neha",  email: "neha@example.com",  color: "#e9627a", role: "member"  },
];

const FALLBACK_COLORS = ["#5bb0a6", "#d98a5b", "#c75b7a", "#7c9a3e", "#3f9ec0"];

function colorForBuilder(email: string, teamMembers?: TeamMemberRow[]): string {
  const known = teamMembers?.find((m) => m.email === email);
  if (known?.color) return known.color;
  const fb = ROSTER_FALLBACK.find((m) => m.email === email);
  if (fb) return fb.color;
  let h = 0;
  for (let i = 0; i < email.length; i += 1) h = (h * 31 + email.charCodeAt(i)) >>> 0;
  return FALLBACK_COLORS[h % FALLBACK_COLORS.length];
}

function nameForEmail(email: string, teamMembers?: TeamMemberRow[]): string {
  const known = teamMembers?.find((m) => m.email === email);
  if (known?.name) return known.name;
  const fb = ROSTER_FALLBACK.find((m) => m.email === email);
  if (fb) return fb.name;
  return email.split("@")[0];
}

const initialOf = (name: string): string => (name.trim()[0] ?? "?").toUpperCase();

function submittedLabel(iso?: string): string {
  if (!iso) return "just now";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";
  const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  return days === 1 ? "yesterday" : `${days}d ago`;
}

function taskToPlacement(t: MockTask, teamMembers?: TeamMemberRow[]): Placement {
  return {
    id: t.id,
    task: t.title,
    builder: nameForEmail(t.assignee, teamMembers),
    builderEmail: t.assignee,
    item: itemForKind(t.component ?? "tree"),
    x: t.worldX ?? 0,
    z: t.worldZ ?? 0,
    state: t.status as PlacementState,
    submitted: submittedLabel(undefined),
  };
}

function rowToTask(r: TaskRow): MockTask {
  return {
    id: r.id,
    title: r.title,
    assignee: r.assignee,
    assigner: r.assigner,
    tier: r.points as Tier,
    source: (r.source ?? "slack") as TaskSource,
    sprintId: r.sprint_id,
    status: r.status as TaskStatus,
    due: r.due ?? "",
    component: r.component,
    worldX: r.world_x,
    worldZ: r.world_z,
  };
}

// Deterministic placements for the GSAP cinematic (no pod writes; replays identically).
function cineSeed(): Placement[] {
  return [
    { id: "c-est-1", task: "Ship the onboarding flow", builder: "Asha",  builderEmail: "asha@example.com",  item: itemForKind("cottage"),     x: -1, z: 0,  state: "established",  submitted: "2h ago" },
    { id: "c-est-2", task: "Polish the review queue",  builder: "Kabir", builderEmail: "kabir@example.com", item: itemForKind("crystals"),    x: 1,  z: 1,  state: "established",  submitted: "1h ago" },
    { id: "c-est-3", task: "Design the recap screen",  builder: "Neha",  builderEmail: "neha@example.com",  item: itemForKind("fountain"),    x: 0,  z: 2,  state: "established",  submitted: "3h ago" },
    { id: "c-rej",   task: "Out-of-scope experiment",  builder: "Rohan", builderEmail: "rohan@example.com", item: itemForKind("castle_gate"), x: 2,  z: -1, state: "under_review", submitted: "just now" },
  ];
}
const CINE_HERO: Placement = {
  id: "c-hero", task: "Prototype the next screen", builder: "Manager", builderEmail: "manager@example.com",
  item: itemForKind("watermill"), x: 0, z: -1, state: "under_review", submitted: "just now",
};

// ---------------------------------------------------------------------------
// 3D primitives + kit loader
// ---------------------------------------------------------------------------

const TILE_GAP = 1.72;
const NO_RAYCAST: THREE.Object3D["raycast"] = () => {};
function tilePosition(x: number, z: number, y = 0.32): [number, number, number] {
  return [x * TILE_GAP, y, z * TILE_GAP];
}

const ASSET_BASE = import.meta.env.BASE_URL;

// Sound bank for cinematic SFX cues, keyed by the `sfx` name used in demos.ts.
const SFX_URLS: Record<string, string> = {
  click: `${ASSET_BASE}sounds/click.mp3`,
  shimmer: `${ASSET_BASE}sounds/shimmer.mp3`,
  // Bright chime on approve — reuses the existing sparkle until a dedicated file lands.
  chime: `${ASSET_BASE}sounds/shimmer.mp3`,
  // The cues below point at files not yet in public/sounds/ — they no-op silently
  // (Audio load fails, play() rejects) until you drop the mp3s in.
  whoosh: `${ASSET_BASE}sounds/whoosh.mp3`,
  crumble: `${ASSET_BASE}sounds/crumble.mp3`,
  swell: `${ASSET_BASE}sounds/swell.mp3`,
};
const kit = {
  fantasy: `${ASSET_BASE}kits/fantasy-town`,
  urban:   `${ASSET_BASE}kits/urban-city`,
  pirate:  `${ASSET_BASE}kits/pirate`,
  cars:    `${ASSET_BASE}kits/cars`,
};

type KitPlacement = {
  url: string;
  position: [number, number, number];
  rotation?: [number, number, number];
  scale?: number | [number, number, number];
};

function KitModel({ url, position, rotation = [0, 0, 0], scale = 1 }: KitPlacement) {
  const { scene } = useGLTF(url);
  const clone = useMemo(() => {
    const cloned = scene.clone(true);
    cloned.traverse((object) => {
      if (object instanceof THREE.Mesh) {
        object.castShadow = true;
        object.receiveShadow = true;
      }
    });
    return cloned;
  }, [scene]);
  return <primitive object={clone} position={position} rotation={rotation} scale={scale} />;
}

const ambientScenery: KitPlacement[] = [
  { url: `${kit.fantasy}/road.glb`,                  position: tilePosition(-3, -2, 0.32), rotation: [0, Math.PI / 2, 0], scale: 0.86 },
  { url: `${kit.fantasy}/road.glb`,                  position: tilePosition(-2, -2, 0.32), rotation: [0, Math.PI / 2, 0], scale: 0.86 },
  { url: `${kit.fantasy}/road-bend.glb`,              position: tilePosition(-1, -2, 0.32), rotation: [0, Math.PI, 0],     scale: 0.86 },
  { url: `${kit.urban}/path-long.glb`,                position: tilePosition(0, -2, 0.34),  rotation: [0, Math.PI / 2, 0], scale: 0.9  },
  { url: `${kit.fantasy}/tree-high.glb`,              position: tilePosition(-5, 1, 0.32),  rotation: [0, 0.1, 0],         scale: 0.78 },
  { url: `${kit.fantasy}/tree-high-round.glb`,        position: tilePosition(-5, 2, 0.32),  rotation: [0, -0.4, 0],        scale: 0.72 },
  { url: `${kit.fantasy}/tree.glb`,                   position: tilePosition(-4, 3, 0.32),  rotation: [0, 0.22, 0],        scale: 0.78 },
  { url: `${kit.urban}/tree-small.glb`,               position: tilePosition(1, 4, 0.32),   rotation: [0, 0.28, 0],        scale: 0.85 },
  { url: `${kit.pirate}/palm-detailed-straight.glb`,  position: tilePosition(5, 2, 0.12),   rotation: [0, -0.2, 0],        scale: 0.82 },
  { url: `${kit.urban}/planter.glb`,                  position: tilePosition(-2, 3, 0.34),  rotation: [0, 0.4, 0],         scale: 0.78 },
  { url: `${kit.fantasy}/hedge-large.glb`,            position: tilePosition(-2, 4, 0.34),  rotation: [0, Math.PI / 2, 0], scale: 0.8  },
  { url: `${kit.fantasy}/banner-red.glb`,             position: tilePosition(1, -3, 0.34),  rotation: [0, -0.3, 0],        scale: 0.74 },
  { url: `${kit.cars}/delivery.glb`,                  position: tilePosition(-3, 1, 0.34),  rotation: [0, -0.22, 0],       scale: 0.64 },
  { url: `${kit.cars}/taxi.glb`,                      position: tilePosition(-2, -3, 0.34), rotation: [0, 0.2, 0],         scale: 0.62 },
  { url: `${kit.cars}/van.glb`,                       position: tilePosition(-4, -2, 0.34), rotation: [0, Math.PI / 2.25, 0], scale: 0.6 },
];

const PRELOAD_URLS = Array.from(
  new Set([
    ...ambientScenery.map((p) => p.url),
    `${kit.pirate}/castle-gate.glb`,
    `${kit.fantasy}/fountain-round.glb`,
    `${kit.fantasy}/windmill.glb`,
    `${kit.fantasy}/watermill.glb`,
    `${kit.fantasy}/stall-red.glb`,
    `${kit.fantasy}/cart-high.glb`,
    `${kit.fantasy}/lantern.glb`,
    `${kit.urban}/building-type-s.glb`,
    `${kit.pirate}/ship-pirate-small.glb`,
    `${kit.pirate}/tower-watch.glb`,
    `${kit.pirate}/tower-complete-large.glb`,
  ]),
);

function AmbientScenery() {
  return (
    <group>
      {ambientScenery.map((placement, index) => (
        <KitModel key={`${placement.url}-${index}`} {...placement} />
      ))}
    </group>
  );
}

function TerrainBlock({ x, z, color, height = 0.34 }: { x: number; z: number; color: string; height?: number }) {
  return (
    <group position={tilePosition(x, z, height / 2 - 0.1)}>
      <mesh castShadow receiveShadow>
        <boxGeometry args={[1.5, height, 1.5]} />
        <meshStandardMaterial color={color} roughness={0.82} />
      </mesh>
      <mesh position={[0, height / 2 + 0.006, 0]} receiveShadow>
        <boxGeometry args={[1.47, 0.025, 1.47]} />
        <meshStandardMaterial color={new THREE.Color(color).offsetHSL(0, 0.06, 0.08)} roughness={0.9} />
      </mesh>
    </group>
  );
}

function Trunk({ position = [0, 0.34, 0] as [number, number, number], scale = 1 }: { position?: [number, number, number]; scale?: number }) {
  return (
    <mesh position={position} scale={scale} castShadow>
      <cylinderGeometry args={[0.1, 0.15, 0.68, 10]} />
      <meshStandardMaterial color="#8f5b32" roughness={0.78} />
    </mesh>
  );
}

function CanopyTree({ color = "#48b85d" }: { color?: string }) {
  return (
    <group>
      <Trunk />
      <mesh position={[0, 0.98, 0]} castShadow>
        <sphereGeometry args={[0.46, 18, 14]} />
        <meshStandardMaterial color={color} roughness={0.76} />
      </mesh>
      <mesh position={[-0.28, 0.82, 0.12]} castShadow>
        <sphereGeometry args={[0.3, 14, 10]} />
        <meshStandardMaterial color={new THREE.Color(color).offsetHSL(0.03, 0.05, 0.03)} roughness={0.76} />
      </mesh>
      <mesh position={[0.28, 0.84, -0.08]} castShadow>
        <sphereGeometry args={[0.3, 14, 10]} />
        <meshStandardMaterial color={new THREE.Color(color).offsetHSL(-0.04, 0.04, 0.02)} roughness={0.76} />
      </mesh>
    </group>
  );
}

function Cottage() {
  return (
    <group>
      <mesh position={[0, 0.38, 0]} castShadow>
        <boxGeometry args={[0.95, 0.76, 0.86]} />
        <meshStandardMaterial color="#f5d69a" roughness={0.82} />
      </mesh>
      <mesh position={[0, 0.88, 0]} rotation={[0, Math.PI / 4, 0]} castShadow>
        <coneGeometry args={[0.78, 0.62, 4]} />
        <meshStandardMaterial color="#d85b45" roughness={0.78} />
      </mesh>
      <mesh position={[0, 0.2, -0.44]} castShadow>
        <boxGeometry args={[0.26, 0.4, 0.04]} />
        <meshStandardMaterial color="#7f5b3d" roughness={0.86} />
      </mesh>
      <mesh position={[-0.31, 0.45, -0.45]} castShadow>
        <boxGeometry args={[0.2, 0.18, 0.035]} />
        <meshStandardMaterial color="#7bc3d6" roughness={0.4} />
      </mesh>
      <mesh position={[0.31, 0.45, -0.45]} castShadow>
        <boxGeometry args={[0.2, 0.18, 0.035]} />
        <meshStandardMaterial color="#7bc3d6" roughness={0.4} />
      </mesh>
    </group>
  );
}

function TacoStand() {
  return (
    <group>
      <mesh position={[0, 0.25, 0]} castShadow>
        <boxGeometry args={[1.0, 0.5, 0.72]} />
        <meshStandardMaterial color="#ffe1a4" roughness={0.78} />
      </mesh>
      <mesh position={[0, 0.62, -0.03]} castShadow>
        <boxGeometry args={[1.16, 0.16, 0.82]} />
        <meshStandardMaterial color="#ef5650" roughness={0.78} />
      </mesh>
      <mesh position={[0, 0.82, 0]} castShadow>
        <boxGeometry args={[1.24, 0.1, 0.9]} />
        <meshStandardMaterial color="#ef5650" roughness={0.78} />
      </mesh>
    </group>
  );
}

function Fountain() {
  return (
    <group>
      <mesh position={[0, 0.15, 0]} castShadow receiveShadow>
        <cylinderGeometry args={[0.52, 0.58, 0.3, 18]} />
        <meshStandardMaterial color="#cfcec1" roughness={0.75} />
      </mesh>
      <mesh position={[0, 0.33, 0]} castShadow>
        <cylinderGeometry args={[0.34, 0.38, 0.12, 18]} />
        <meshStandardMaterial color="#69c7dc" roughness={0.44} transparent opacity={0.86} />
      </mesh>
      <mesh position={[0, 0.6, 0]} castShadow>
        <cylinderGeometry args={[0.08, 0.12, 0.58, 12]} />
        <meshStandardMaterial color="#b8b8ad" roughness={0.8} />
      </mesh>
      <mesh position={[0, 0.94, 0]} castShadow>
        <sphereGeometry args={[0.16, 14, 10]} />
        <meshStandardMaterial color="#6ed8ed" transparent opacity={0.88} />
      </mesh>
    </group>
  );
}

function CrystalRocks() {
  return (
    <group>
      {(
        [
          [-0.22, 0.28, 0.08, 0.26, "#59d6dc"],
          [0.1, 0.38, -0.08, 0.32, "#7c6ee6"],
          [0.32, 0.22, 0.18, 0.22, "#72e3a3"],
        ] as [number, number, number, number, string][]
      ).map(([x, y, z, scale, color], index) => (
        <mesh key={index} position={[x, y, z]} scale={scale} castShadow>
          <octahedronGeometry args={[1, 0]} />
          <meshStandardMaterial color={color} roughness={0.42} metalness={0.08} />
        </mesh>
      ))}
    </group>
  );
}

function MushroomPatch() {
  return (
    <group>
      {(
        [
          [-0.26, 0.18, 0.1, 0.24],
          [0.0, 0.23, -0.1, 0.28],
          [0.27, 0.16, 0.16, 0.2],
        ] as [number, number, number, number][]
      ).map(([x, y, z, scale], index) => (
        <group key={index} position={[x, 0, z]} scale={scale}>
          <mesh position={[0, y, 0]} castShadow>
            <cylinderGeometry args={[0.16, 0.12, 0.58, 8]} />
            <meshStandardMaterial color="#f4d7a8" roughness={0.88} />
          </mesh>
          <mesh position={[0, y + 0.32, 0]} castShadow>
            <sphereGeometry args={[0.42, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2]} />
            <meshStandardMaterial color={index === 1 ? "#e85764" : "#f2a33d"} roughness={0.82} />
          </mesh>
        </group>
      ))}
    </group>
  );
}

function TileSelector({ occupied, onSelect }: {
  occupied: Set<string>;
  onSelect: (x: number, z: number) => void;
}) {
  const meshRefs = useRef<(THREE.Mesh | null)[]>([]);
  const validTiles = terrainTiles.filter(([x, z]) => !occupied.has(`${x},${z}`));
  useFrame((state) => {
    const opacity = 0.22 + Math.sin(state.clock.elapsedTime * 2.8) * 0.1;
    meshRefs.current.forEach((m) => {
      if (m?.material instanceof THREE.MeshStandardMaterial) {
        m.material.opacity = opacity;
      }
    });
  });
  return (
    <group>
      {validTiles.map(([x, z], i) => (
        <mesh
          key={`ts-${x}:${z}`}
          ref={(el) => { meshRefs.current[i] = el; }}
          position={tilePosition(x, z, 0.47)}
          onClick={(e) => { e.stopPropagation(); onSelect(x, z); }}
          onPointerOver={() => { document.body.style.cursor = "cell"; }}
          onPointerOut={() => { document.body.style.cursor = "auto"; }}
        >
          <boxGeometry args={[1.42, 0.06, 1.42]} />
          <meshStandardMaterial
            color="#7dd8ff"
            emissive="#4fbfff"
            emissiveIntensity={0.5}
            transparent
            opacity={0.28}
          />
        </mesh>
      ))}
    </group>
  );
}

function Rubble() {
  const rocks: [number, number, number, number][] = [
    [-0.22, 0.1, 0.02, 0.22],
    [0.2, 0.12, 0.12, 0.26],
    [0.04, 0.08, -0.22, 0.18],
    [-0.06, 0.14, 0.2, 0.2],
  ];
  return (
    <group>
      {rocks.map(([x, y, z, s], i) => (
        <mesh key={i} position={[x, y, z]} rotation={[i, i * 0.7, i * 0.3]} castShadow receiveShadow>
          <dodecahedronGeometry args={[s, 0]} />
          <meshStandardMaterial color={i % 2 ? "#a6937b" : "#c3b393"} roughness={0.96} />
        </mesh>
      ))}
    </group>
  );
}

function ReviewMarker({ top = 1.9 }: { top?: number }) {
  const ring = useRef<THREE.Mesh>(null);
  const pin = useRef<THREE.Group>(null);
  useFrame((state) => {
    const t = state.clock.elapsedTime;
    if (ring.current) {
      const s = 1 + Math.sin(t * 2.2) * 0.1;
      ring.current.scale.set(s, s, 1);
      (ring.current.material as THREE.MeshStandardMaterial).opacity = 0.5 + 0.4 * (0.5 + 0.5 * Math.sin(t * 2.2));
    }
    if (pin.current) {
      pin.current.position.y = top + Math.sin(t * 2.4) * 0.08;
      pin.current.rotation.y = t * 0.9;
    }
  });
  return (
    <group>
      <mesh ref={ring} position={[0, 0.1, 0]} rotation={[-Math.PI / 2, 0, 0]} raycast={NO_RAYCAST}>
        <torusGeometry args={[0.66, 0.06, 12, 48]} />
        <meshStandardMaterial color="#ffce4a" emissive="#efad32" emissiveIntensity={1.1} transparent />
      </mesh>
      <group ref={pin} position={[0, top, 0]}>
        <mesh position={[0, 0.12, 0]} castShadow raycast={NO_RAYCAST}>
          <sphereGeometry args={[0.2, 16, 14]} />
          <meshStandardMaterial color="#ffd76a" emissive="#efad32" emissiveIntensity={0.6} />
        </mesh>
        <mesh position={[0, -0.08, 0]} rotation={[Math.PI, 0, 0]} castShadow raycast={NO_RAYCAST}>
          <coneGeometry args={[0.14, 0.24, 16]} />
          <meshStandardMaterial color="#ffd76a" emissive="#efad32" emissiveIntensity={0.6} />
        </mesh>
        <mesh position={[0, 0.13, 0.205]} raycast={NO_RAYCAST}>
          <circleGeometry args={[0.09, 16]} />
          <meshStandardMaterial color="#7f5a17" />
        </mesh>
      </group>
    </group>
  );
}

function SelectRing({ color = "#3fa3df" }: { color?: string }) {
  const ring = useRef<THREE.Mesh>(null);
  useFrame((state) => {
    if (!ring.current) return;
    const s = 1 + Math.sin(state.clock.elapsedTime * 3) * 0.06;
    ring.current.scale.set(s, s, 1);
  });
  return (
    <mesh ref={ring} position={[0, 0.08, 0]} rotation={[-Math.PI / 2, 0, 0]} raycast={NO_RAYCAST}>
      <torusGeometry args={[0.78, 0.05, 12, 48]} />
      <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.9} transparent opacity={0.9} />
    </mesh>
  );
}

function Burst() {
  const grp = useRef<THREE.Group>(null);
  const life = useRef(0);
  const seeds = useMemo(
    () =>
      Array.from({ length: 16 }, (_, i) => {
        const a = (i / 16) * Math.PI * 2;
        return { x: Math.cos(a), z: Math.sin(a), y: 0.7 + (i % 3) * 0.25, s: 0.4 + (i % 4) * 0.12 };
      }),
    [],
  );
  useFrame((_, dt) => {
    life.current += dt * 1.5;
    const t = Math.min(1, life.current);
    if (grp.current) {
      grp.current.children.forEach((c, i) => {
        const p = seeds[i];
        c.position.set(p.x * t * 1.0, p.y * t + 0.5, p.z * t * 1.0);
        const mesh = c as THREE.Mesh;
        mesh.scale.setScalar(Math.max(0.0001, p.s * (1 - t) * 0.32));
        (mesh.material as THREE.MeshStandardMaterial).opacity = 1 - t;
      });
    }
  });
  return (
    <group ref={grp}>
      {seeds.map((_, i) => (
        <mesh key={i} raycast={NO_RAYCAST}>
          <sphereGeometry args={[1, 8, 8]} />
          <meshStandardMaterial
            color={i % 2 ? "#ffd76a" : "#7be0a0"}
            emissive={i % 2 ? "#efad32" : "#42be65"}
            emissiveIntensity={0.7}
            transparent
          />
        </mesh>
      ))}
    </group>
  );
}

function KindModel({ kind }: { kind: ComponentKind }) {
  switch (kind) {
    case "tree":        return <CanopyTree color="#48b85d" />;
    case "sapling":     return <group scale={0.66}><CanopyTree color="#69cf7e" /></group>;
    case "fountain":    return <Fountain />;
    case "cottage":     return <Cottage />;
    case "taco_stand":  return <TacoStand />;
    case "crystals":    return <CrystalRocks />;
    case "mushrooms":   return <MushroomPatch />;
    case "castle_gate": return <KitModel url={`${kit.pirate}/castle-gate.glb`}       position={[0, 0.3, 0]} rotation={[0, -0.3, 0]} scale={0.5} />;
    case "ship":        return <KitModel url={`${kit.pirate}/ship-pirate-small.glb`}  position={[0.3, 0.08, 0.3]} rotation={[0, -0.6, 0]} scale={0.28} />;;
    case "watchtower":  return <KitModel url={`${kit.pirate}/tower-watch.glb`}        position={[0, 0.3, 0]} scale={0.6} />;
    case "windmill":    return <KitModel url={`${kit.pirate}/tower-complete-large.glb`} position={[0, 0.3, 0]} rotation={[0, 0.5, 0]} scale={0.44} />;
    case "watermill":   return <KitModel url={`${kit.fantasy}/watermill.glb`}         position={[0, 0.3, 0]} scale={0.65} />;
    case "stall":       return <KitModel url={`${kit.fantasy}/stall-red.glb`}         position={[0, 0.3, 0]} rotation={[0, 0.3, 0]} scale={0.76} />;
    case "cart":        return <KitModel url={`${kit.fantasy}/cart-high.glb`}         position={[0, 0.3, 0]} rotation={[0, 0.4, 0]} scale={0.64} />;
    case "lantern":     return <KitModel url={`${kit.fantasy}/lantern.glb`}           position={[0, 0.3, 0]} scale={0.70} />;
    case "manor":       return <KitModel url={`${kit.urban}/building-type-s.glb`}     position={[0, 0.3, 0]} rotation={[0, -0.18, 0]} scale={0.74} />;
    case "grand_fountain": return <KitModel url={`${kit.fantasy}/fountain-round.glb`} position={[0, 0.3, 0]} rotation={[0, 0.4, 0]} scale={0.95} />;
    default:            return <CanopyTree />;
  }
}

function easeOutBack(x: number) {
  const c1 = 1.70158,
    c3 = c1 + 1;
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
}

function PlacedObject({
  placement,
  selected,
  onHover,
}: {
  placement: Placement;
  selected: boolean;
  onHover: (id: string | null, sx?: number, sy?: number) => void;
}) {
  const obj = useRef<THREE.Group>(null);
  const grow = useRef(0);
  const pop = useRef(0);
  const demo = useRef(placement.state === "demolished" ? 1 : 0);
  const prev = useRef<PlacementState>(placement.state);
  const [burstKey, setBurstKey] = useState(0);

  const baseScale = placement.item.kind === "cottage" ? 1.05 : 1;
  const tall =
    placement.item.kind === "castle_gate" ||
    placement.item.kind === "watchtower" ||
    placement.item.kind === "windmill";

  useEffect(() => {
    if (prev.current !== placement.state) {
      if (placement.state === "established" && prev.current === "under_review") {
        pop.current = 1;
        setBurstKey((k) => k + 1);
      }
      if (placement.state === "demolished") demo.current = 0.0001;
      prev.current = placement.state;
    }
  }, [placement.state]);

  useFrame((_, dt) => {
    grow.current = Math.min(1, grow.current + dt * 2.2);
    if (pop.current > 0) pop.current = Math.max(0, pop.current - dt * 2);
    if (demo.current > 0 && demo.current < 1) demo.current = Math.min(1, demo.current + dt * 2.4);
    if (!obj.current) return;
    const gEase = Math.max(0, easeOutBack(grow.current));
    const popAmt = pop.current > 0 ? Math.sin(pop.current * Math.PI) * 0.2 : 0;
    if (placement.state === "demolished") {
      obj.current.scale.setScalar(baseScale * gEase * (1 - demo.current));
      obj.current.rotation.z = demo.current * 0.5;
      obj.current.position.y = -demo.current * 0.15;
    } else {
      obj.current.scale.setScalar(baseScale * gEase * (1 + popAmt));
      obj.current.rotation.z = 0;
      obj.current.position.y = 0;
    }
  });

  const pending = placement.state === "under_review";
  const demolished = placement.state === "demolished";

  return (
    <group
      position={tilePosition(placement.x, placement.z)}
      onPointerOver={(e) => {
        e.stopPropagation();
        if (demolished) return;
        onHover(placement.id, e.nativeEvent.clientX, e.nativeEvent.clientY);
        document.body.style.cursor = "pointer";
      }}
      onPointerOut={(e) => {
        e.stopPropagation();
        onHover(null);
        document.body.style.cursor = "auto";
      }}
    >
      <group ref={obj}>
        <Suspense fallback={null}>{demolished ? <Rubble /> : <KindModel kind={placement.item.kind} />}</Suspense>
      </group>
      {pending && <ReviewMarker top={tall ? 2.7 : 1.9} />}
      {selected && !demolished && <SelectRing color={pending ? "#efad32" : "#3fa3df"} />}
      {burstKey > 0 && placement.state === "established" && <Burst key={burstKey} />}
    </group>
  );
}

function Water() {
  return (
    <mesh position={[5.2, -0.18, 0]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[4.4, 7.2]} />
      <meshStandardMaterial color="#55c0d7" roughness={0.5} metalness={0.05} transparent opacity={0.84} />
    </mesh>
  );
}

const terrainTiles = Array.from({ length: 99 }, (_, index) => {
  const x = (index % 11) - 5;
  const z = Math.floor(index / 11) - 4;
  return [x, z] as const;
}).filter(([x, z]) => Math.abs(x + z) < 8 && !(x >= 5 && z <= 2));

function CameraRig({ target }: { target: THREE.Vector3 | null }) {
  const controls = useThree((s) => s.controls) as unknown as { target: THREE.Vector3; update?: () => void } | null;
  const home = useMemo(() => new THREE.Vector3(0, 0.05, 0), []);
  useFrame(() => {
    if (!controls) return;
    controls.target.lerp(target ?? home, 0.07);
    controls.update?.();
  });
  return null;
}

function World({
  zoom,
  placements,
  onHover,
  selectedId,
  selectedPos,
}: {
  zoom: number;
  placements: Placement[];
  onHover: (id: string | null, sx?: number, sy?: number) => void;
  selectedId: string | null;
  selectedPos: THREE.Vector3 | null;
}) {
  return (
    <>
      <color attach="background" args={["#bdeaf5"]} />
      <fog attach="fog" args={["#cdeffa", 15, 34]} />
      <OrthographicCamera makeDefault position={[8.8, 10.4, 8.8]} zoom={zoom} />
      <OrbitControls
        makeDefault
        enablePan={false}
        autoRotate={!selectedPos}
        autoRotateSpeed={0.22}
        enableDamping
        dampingFactor={0.05}
        minPolarAngle={0.5}
        maxPolarAngle={0.95}
        minZoom={34}
        maxZoom={120}
        target={[0, 0.05, 0]}
      />
      <CameraRig target={selectedPos} />
      <ambientLight intensity={1.18} />
      <directionalLight
        position={[5, 8.5, 5]}
        intensity={2.05}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-camera-left={-9}
        shadow-camera-right={9}
        shadow-camera-top={9}
        shadow-camera-bottom={-9}
      />
      <hemisphereLight args={["#eafcff", "#74ad63", 1.05]} />
      <Water />
      {terrainTiles.map(([x, z]) => {
        const here = placements.find((p) => p.x === x && p.z === z);
        const color =
          here?.state === "under_review" ? "#9bd97f" : here?.state === "demolished" ? "#7fae5f" : "#86cf66";
        const height = here && here.state !== "demolished" ? 0.42 : 0.34;
        return <TerrainBlock key={`${x}:${z}`} x={x} z={z} color={color} height={height} />;
      })}
      <Suspense fallback={null}>
        <AmbientScenery />
      </Suspense>
      {placements.map((placement) => (
        <PlacedObject
          key={placement.id}
          placement={placement}
          selected={selectedId === placement.id}
          onHover={onHover}
        />
      ))}

      <Text
        position={[0, 0.2, 6.4]}
        rotation={[-0.75, 0, 0]}
        fontSize={0.3}
        color="#2f5a36"
        anchorX="center"
        anchorY="middle"
      >
        Task Arcade
      </Text>
      <ContactShadows position={[0, -0.04, 0]} opacity={0.42} scale={13} blur={2.8} far={4} />
    </>
  );
}

// ---------------------------------------------------------------------------
// UI helpers
// ---------------------------------------------------------------------------

function KindIcon({ kind }: { kind: ComponentKind }) {
  if (kind === "ship") return <Ship size={18} />;
  if (kind === "windmill" || kind === "watermill") return <Wind size={18} />;
  if (kind === "fountain" || kind === "grand_fountain") return <Droplets size={18} />;
  if (kind === "manor") return <Building2 size={18} />;
  if (kind === "cottage" || kind === "castle_gate" || kind === "watchtower") return <Castle size={18} />;
  if (kind === "stall" || kind === "taco_stand" || kind === "cart") return <Store size={18} />;
  if (kind === "crystals") return <Gem size={18} />;
  return <Trees size={18} />;
}

function TierBadge({ tier }: { tier: Tier }) {
  return (
    <span className="tier-badge" style={{ background: TIER_COLOR[tier] }}>
      {tier}
    </span>
  );
}

function Avatar({ name, color, email, size = 34 }: { name: string; color: string; email?: string; size?: number }) {
  const [imgError, setImgError] = useState(false);
  if (email && !imgError) {
    return (
      <span className="avatar avatar--gravatar" style={{ width: size, height: size }}>
        <img
          src={gravatarUrl(email, size * 2)}
          alt={name}
          width={size}
          height={size}
          onError={() => setImgError(true)}
          style={{ borderRadius: "50%", objectFit: "cover", display: "block" }}
        />
      </span>
    );
  }
  return (
    <span className="avatar" style={{ background: color, width: size, height: size, fontSize: size * 0.41 }}>
      {initialOf(name)}
    </span>
  );
}

function SourceBadge({ source }: { source: TaskSource }) {
  const cfg = SOURCE_CFG[source];
  return (
    <span className="source-badge" style={{ background: cfg.bg }}>
      {cfg.icon}
      {cfg.label}
    </span>
  );
}

function TaskStatusDot({ status }: { status: TaskStatus }) {
  if (status === "cleared") return <span className="task-dot task-dot--cleared"><Check size={10} /></span>;
  if (status === "under_review")  return <span className="task-dot task-dot--pending" />;
  if (status === "established")  return <span className="task-dot task-dot--cleared"><Check size={10} /></span>;
  if (status === "demolished")  return <span className="task-dot task-dot--demolished" />;
  return <span className="task-dot task-dot--assigned"><Circle size={10} /></span>;
}


// Floating receipt overlay — DOM element outside the Canvas, pointer-events:none
// so it can never steal canvas hover events (avoids drei <Html> flicker bug).
function HoverReceipt({ placement, x, y }: { placement: Placement; x: number; y: number }) {
  const pending = placement.state === "under_review";
  return (
    <div
      className={`receipt receipt--floating ${pending ? "is-pending" : "is-built"}`}
      style={{ left: x + 16, top: y - 12 }}
    >
      <div className="receipt-head">
        <span className="receipt-av" style={{ background: colorForBuilder(placement.builderEmail) }}>
          {initialOf(placement.builder)}
        </span>
        <div>
          <strong>{placement.builder}</strong>
          <small>{placement.submitted}</small>
        </div>
        <span className="receipt-tier" style={{ background: TIER_COLOR[placement.item.tier] }}>
          {placement.item.tier}
        </span>
      </div>
      <p className="receipt-task">{placement.task}</p>
      <div className="receipt-foot">
        <span>{placement.item.label}</span>
        <span className={pending ? "rev-pending" : "rev-built"}>
          {pending ? "● Pending review" : "✓ Established"}
        </span>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Build picker modal — opens when a member clears a task. Shows the catalogue
// filtered to the cleared task's point tier; picking one places it (under review).
// ---------------------------------------------------------------------------

function BuildPickerModal({
  title,
  tier,
  catalogue,
  onPick,
  onClose,
}: {
  title: string;
  tier: Tier;
  catalogue: CatalogueItem[];
  onPick: (item: CatalogueItem) => void;
  onClose: () => void;
}) {
  const options = catalogue.filter((c) => c.tier === tier);
  return (
    <div className="build-modal-overlay" onClick={onClose}>
      <div
        className="build-modal"
        role="dialog"
        aria-label="Choose your build"
        onClick={(e) => e.stopPropagation()}
      >
        <button className="build-modal-close" type="button" aria-label="Close" onClick={onClose}>
          <X size={18} />
        </button>
        <div className="build-modal-eyebrow">
          <CheckCircle2 size={15} /> Task cleared · {tier} pts unlocked
        </div>
        <h2 className="build-modal-title">Pick what you'll build</h2>
        <p className="build-modal-sub">
          “{title}” earned <strong>{tier} points</strong>. Choose a {tier}-pt monument to raise in
          your team's world — your manager approves it into permanence.
        </p>
        <div className="build-modal-grid">
          {options.map((c) => (
            <button key={c.kind} className="build-option" type="button" onClick={() => onPick(c)}>
              <span
                className="build-option-icon"
                style={{ background: `${TIER_COLOR[c.tier]}20`, color: TIER_COLOR[c.tier] }}
              >
                <KindIcon kind={c.kind} />
              </span>
              <strong>{c.label}</strong>
              <TierBadge tier={c.tier} />
            </button>
          ))}
        </div>
        <p className="build-modal-foot">
          <Sparkles size={13} /> Bigger tasks unlock bigger builds.
        </p>
      </div>
    </div>
  );
}

function AssignTaskModal({ teamMembers, activeSprintId, onSubmit, onClose }: {
  teamMembers: TeamMemberRow[];
  activeSprintId: string;
  onSubmit: (title: string, assigneeEmail: string, points: Tier, due: string) => void;
  onClose: () => void;
}) {
  const members = teamMembers.filter((m) => m.role === "member" || m.role === "manager");
  const [title, setTitle] = useState("");
  const [assignee, setAssignee] = useState(members[0]?.email ?? "");
  const [points, setPoints] = useState<Tier>(30);
  const [due, setDue] = useState("");
  return (
    <div className="build-modal-overlay" onClick={onClose}>
      <div className="build-modal" role="dialog" onClick={(e) => e.stopPropagation()}>
        <button className="build-modal-close" type="button" onClick={onClose}><X size={18} /></button>
        <div className="build-modal-eyebrow"><Plus size={15} /> Manager · assign task</div>
        <h2 className="build-modal-title">New task</h2>
        <div className="assign-form">
          <label className="assign-label">
            Task
            <input
              className="assign-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="What needs to be done?"
              autoFocus
            />
          </label>
          <label className="assign-label">
            Assignee
            <select className="assign-select" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
              {members.map((m) => <option key={m.email} value={m.email}>{m.name}</option>)}
            </select>
          </label>
          <label className="assign-label">
            Points
            <div className="tier-picker">
              {([15, 30, 45, 60] as Tier[]).map((t) => (
                <button
                  key={t}
                  type="button"
                  className={`tier-btn${points === t ? " active" : ""}`}
                  style={points === t ? { background: TIER_COLOR[t], color: "#fff" } : {}}
                  onClick={() => setPoints(t)}
                >
                  {t} pts
                </button>
              ))}
            </div>
          </label>
          <label className="assign-label">
            Due
            <input
              className="assign-input"
              value={due}
              onChange={(e) => setDue(e.target.value)}
              placeholder="e.g. Tomorrow, Jun 30"
            />
          </label>
        </div>
        <button
          className="place-button"
          type="button"
          disabled={!title.trim()}
          onClick={() => onSubmit(title.trim(), assignee, points, due)}
        >
          <Send size={16} /> Assign task
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// App
// ---------------------------------------------------------------------------

function App() {
  const [qmRoute, setQmRoute] = useState(() =>
    typeof window !== "undefined" && window.location.hash.replace(/^#\/?/, "") === "quartermaster",
  );
  useEffect(() => {
    const onHash = () => {
      setQmRoute(window.location.hash.replace(/^#\/?/, "") === "quartermaster");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const [activeTab, setActiveTab] = useState<AppTab>("world");
  const [worldZoom, setWorldZoom] = useState(60);
  const [hovered, setHovered] = useState<{ id: string; x: number; y: number } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ id: number; text: string; tone: "good" | "bad" } | null>(null);
  const [busy, setBusy] = useState(false);
  const [taskView, setTaskView] = useState<"my" | "team">("my");
  // Optimistic overrides: show approve/reject result immediately before API confirms
  const [localStates, setLocalStates] = useState<Record<string, PlacementState>>({});
  const [cinematic, setCinematic] = useState(
    () => typeof window !== "undefined" && new URLSearchParams(window.location.search).has("cinematic"),
  );
  const [scripted, setScripted] = useState<Placement[]>(() => cineSeed());
  // The cinematic walkthrough currently playing (chosen from the Demos tab).
  const [activeScript, setActiveScript] = useState<CineScript | null>(null);
  const stageRef = useRef<HTMLElement | null>(null);
  const [placingShimmer, setPlacingShimmer] = useState(false);
  const [optimisticPlacements, setOptimisticPlacements] = useState<Placement[]>([]);
  // Build picker: set when a task is cleared; carries the task title + its point tier.
  const [buildModal, setBuildModal] = useState<{ title: string; tier: Tier } | null>(null);
  // Tasks the member has marked done this session (drives the cleared check + hides "Mark done").
  const [clearedIds, setClearedIds] = useState<Set<string>>(new Set());

  // Audio — Web Audio API for zero-latency, overlappable SFX. HTML5 <audio>.play()
  // has decode/dispatch lag and can't retrigger instantly; decoding each clip into
  // an AudioBuffer once and firing a fresh BufferSource per trigger removes the lag.
  const audioCtxRef = useRef<AudioContext | null>(null);
  const bufferCacheRef = useRef<Record<string, AudioBuffer>>({});
  const getAudioCtx = useCallback(() => {
    if (!audioCtxRef.current) {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (Ctx) audioCtxRef.current = new Ctx();
    }
    return audioCtxRef.current;
  }, []);
  // Decode every known clip up front so the very first play has no decode latency.
  useEffect(() => {
    const ctx = getAudioCtx();
    if (!ctx) return;
    Object.entries(SFX_URLS).forEach(([name, url]) => {
      fetch(url)
        .then((r) => r.arrayBuffer())
        .then((data) => ctx.decodeAudioData(data))
        .then((buf) => { bufferCacheRef.current[name] = buf; })
        .catch(() => {});
    });
  }, [getAudioCtx]);
  const playBuffer = useCallback(
    (name: string, volume: number) => {
      const ctx = audioCtxRef.current;
      const buf = bufferCacheRef.current[name];
      if (!ctx || !buf) return;
      // Browsers start the context suspended until a user gesture; resume on demand.
      if (ctx.state === "suspended") ctx.resume().catch(() => {});
      const src = ctx.createBufferSource();
      src.buffer = buf;
      const gain = ctx.createGain();
      gain.gain.value = volume;
      src.connect(gain).connect(ctx.destination);
      src.start(0);
    },
    [],
  );
  const playClick = useCallback(() => playBuffer("click", 0.5), [playBuffer]);
  const playShimmer = useCallback(() => playBuffer("shimmer", 0.85), [playBuffer]);
  // Cinematic SFX cues: beats reference a clip by name (add to SFX_URLS + a `sfx`
  // beat in demos.ts to wire a new cue). Unknown names no-op.
  const playSfx = useCallback((name: string) => playBuffer(name, 0.82), [playBuffer]);

  // ── Auth: current user ──
  const { user: authUser } = useAuth(client);
  const currentUserEmail = authUser?.email ?? "";

  // ── Team members from pod ──
  const { records: teamMemberRecords, refresh: refreshTeamMembers } = useRecords<TeamMemberRow>({
    client,
    tableName: "team_members",
    limit: 100,
  });
  const teamMembers = useMemo(() => teamMemberRecords, [teamMemberRecords]);

  // Derive current user's role + display name from team_members
  const myMemberRow = useMemo(
    () => teamMembers.find((m) => m.email === currentUserEmail),
    [teamMembers, currentUserEmail],
  );
  const currentUser = useMemo(() => {
    const role: Role = myMemberRow?.role ?? "viewer";
    const name: string = myMemberRow?.name ?? authUser?.name ?? (currentUserEmail ? currentUserEmail.split("@")[0] : "Guest");
    return { email: currentUserEmail, name, role };
  }, [myMemberRow, authUser, currentUserEmail]);
  const isManager = currentUser.role === "manager";
  const isViewer = currentUser.role === "viewer";

  // ── Sprints from pod ──
  const { records: sprintRecords } = useRecords<SprintRow>({
    client,
    tableName: "sprints",
    sort: [{ field: "starts_at", direction: "desc" }],
    limit: 50,
  });
  const sprints = useMemo(() => sprintRecords, [sprintRecords]);
  const activeSprint = useMemo(() => sprints.find((s) => s.status === "active") ?? sprints[0] ?? null, [sprints]);
  const currentSprint = activeSprint;

  // ── Catalogue from pod (with hardcoded fallback) ──
  const { records: catalogueRecords } = useRecords<CatalogueItemRow>({
    client,
    tableName: "catalogue_items",
    limit: 100,
  });
  const catalogue = useMemo(() => {
    if (catalogueRecords.length > 0) {
      return catalogueRecords.map((r) => ({ kind: r.kind as ComponentKind, label: r.label, tier: r.tier as Tier }));
    }
    return CATALOGUE;
  }, [catalogueRecords]);
  const catalogueByTier = useMemo(() => {
    const tiers: Tier[] = [15, 30, 45, 60];
    return tiers.map((tier) => ({ tier, items: catalogue.filter((c) => c.tier === tier) }));
  }, [catalogue]);

  // ── Live task data from the pod ──
  const { records: taskRecords, refresh: refreshTasks, isLoading, error } = useRecords<TaskRow>({
    client,
    tableName: "tasks",
    sort: [{ field: "created_at", direction: "asc" }],
    limit: 500,
  });
  const allTasks = useMemo(() => taskRecords.map(rowToTask), [taskRecords]);

  // Tasks for the currently selected sprint
  const sprintTasks = useMemo(
    () => currentSprint ? allTasks.filter((t) => t.sprintId === currentSprint.id) : [],
    [allTasks, currentSprint],
  );

  // Derive world placements from tasks (status is under_review/established/demolished + has coords)
  const placementsRaw = useMemo(
    () =>
      sprintTasks
        .filter((t) => t.component && t.worldX != null && t.worldZ != null)
        .map((t) => taskToPlacement(t, teamMembers)),
    [sprintTasks, teamMembers],
  );

  // Merge optimistic overrides so clicks feel instant
  const placements = useMemo(
    () =>
      placementsRaw.map((p) => ({
        ...p,
        state: (localStates[p.id] ?? p.state) as PlacementState,
      })),
    [placementsRaw, localStates],
  );
  // In cinematic mode the world reads from the deterministic scripted set instead of the pod.
  const view = useMemo(() => {
    if (cinematic) return scripted;
    const realIds = new Set(placements.map((p) => p.id));
    return [...placements, ...optimisticPlacements.filter((p) => !realIds.has(p.id))];
  }, [cinematic, scripted, placements, optimisticPlacements]);
  const sprintGoal = cinematic ? 180 : (currentSprint?.goal ?? 1490);
  const cine: CineControls = useMemo(
    () => ({
      reset: () => { setScripted(cineSeed()); setSelectedId(null); setActiveTab("world"); },
      world: () => setActiveTab("world"),
      review: () => setActiveTab("review"),
      spotlightFeature: () => setSelectedId("c-est-1"),
      spotlightHero: () => setSelectedId("c-hero"),
      spotlightReject: () => setSelectedId("c-rej"),
      unspotlight: () => setSelectedId(null),
      placeHero: () => setScripted((s) => (s.some((p) => p.id === "c-hero") ? s : [...s, CINE_HERO])),
      approveHero: () => setScripted((s) => s.map((p) => (p.id === "c-hero" ? { ...p, state: "established" } : p))),
      demolishReject: () => setScripted((s) => s.map((p) => (p.id === "c-rej" ? { ...p, state: "demolished" } : p))),
    }),
    [],
  );

  const reviewFn = useFunctionRun({ client, functionName: "review_task" });
  const placeFn = useFunctionRun({ client, functionName: "place_component" });
  const assignTaskFn = useFunctionRun({ client, functionName: "assign_task" });
  const clearTaskFn = useFunctionRun({ client, functionName: "clear_task" });

  // Derived task lists (filtered by current sprint + current user's email)
  const myTasks = useMemo(
    () => sprintTasks.filter((t) => t.assignee === currentUser.email),
    [sprintTasks, currentUser.email],
  );
  const teamTasks = useMemo(
    () => sprintTasks.filter((t) => t.assignee !== currentUser.email),
    [sprintTasks, currentUser.email],
  );
  // World card hero: the current user's first assigned task
  const myHeroTask = useMemo(
    () => myTasks.find((t) => !clearedIds.has(t.id) && t.status === "assigned") ?? null,
    [myTasks, clearedIds],
  );

  const [assignModalOpen, setAssignModalOpen] = useState(false);

  const placed = view.filter((p) => p.state !== "demolished").length;
  const pending = view.filter((p) => p.state === "under_review");
  const pendingCount = pending.length;
  const established = view.filter((p) => p.state === "established");
  const demolished = view.filter((p) => p.state === "demolished");
  const myPendingBuild = view.find((p) => p.builderEmail === currentUser.email && p.state === "under_review") ?? null;

  const members: Member[] = useMemo(() => {
    const pts: Record<string, number> = {};
    view.filter((p) => p.state === "established").forEach((p) => {
      pts[p.builderEmail] = (pts[p.builderEmail] ?? 0) + p.item.tier;
    });
    const assignees = new Set<string>([
      ...sprintTasks.map((t) => t.assignee),
      ...view.map((p) => p.builderEmail),
    ]);
    const allMembers = teamMembers.length > 0 ? teamMembers : ROSTER_FALLBACK.map((r) => ({ id: r.email, name: r.name, email: r.email, role: r.role as MemberRole, color: r.color, created_at: "" }));
    return allMembers
      .filter((m) => assignees.has(m.email))
      .map((m) => ({
        name: m.name,
        email: m.email,
        color: m.color ?? colorForBuilder(m.email, teamMembers),
        points: pts[m.email] ?? 0,
      }));
  }, [view, teamMembers, sprintTasks]);
  const ranked = [...members].sort((a, b) => b.points - a.points);
  const done = members.reduce((sum, m) => sum + m.points, 0);
  const pct = Math.min(100, Math.round((done / sprintGoal) * 100));

  const selectedPos = useMemo(() => {
    if (!selectedId) return null;
    const p = view.find((x) => x.id === selectedId);
    if (!p) return null;
    const [x, y, z] = tilePosition(p.x, p.z, 0.7);
    return new THREE.Vector3(x, y, z);
  }, [selectedId, view]);

  const hoveredPlacement = hovered ? (view.find((p) => p.id === hovered.id) ?? null) : null;

  useEffect(() => {
    if (!toast) return;
    const id = setTimeout(() => setToast((t) => (t && t.id === toast.id ? null : t)), 3400);
    return () => clearTimeout(id);
  }, [toast]);

  useEffect(() => {
    if (error) setToast({ id: Date.now(), text: "Couldn't load the world from the pod", tone: "bad" });
  }, [error]);

  const flash = useCallback((text: string, tone: "good" | "bad") => {
    setToast({ id: Date.now(), text, tone });
  }, []);

  const handleHover = useCallback((id: string | null, sx?: number, sy?: number) => {
    if (id === null || sx === undefined || sy === undefined) {
      setHovered(null);
    } else {
      setHovered({ id, x: sx, y: sy });
    }
  }, []);

  const decide = useCallback(
    async (placementId: string, decision: "approve" | "reject") => {
      const p = placementsRaw.find((x) => x.id === placementId);
      if (!p || busy) return;
      setSelectedId(null);
      const nextState: PlacementState = decision === "approve" ? "established" : "demolished";
      setLocalStates((prev) => ({ ...prev, [placementId]: nextState }));
      setBusy(true);
      try {
        await reviewFn.start({ task_id: placementId, decision });
        await refreshTasks();
        setLocalStates((prev) => {
          const n = { ...prev };
          delete n[placementId];
          return n;
        });
        flash(
          decision === "approve"
            ? `Approved — ${p.builder}'s ${p.item.label.toLowerCase()} is now part of the world`
            : `Demolished — ${p.builder}'s ${p.item.label.toLowerCase()} sent to rubble`,
          decision === "approve" ? "good" : "bad",
        );
      } catch {
        setLocalStates((prev) => {
          const n = { ...prev };
          delete n[placementId];
          return n;
        });
        flash("Couldn't save the review — try again", "bad");
      } finally {
        setBusy(false);
      }
    },
    [placementsRaw, busy, reviewFn, refreshTasks, flash],
  );

  // Place a chosen catalogue component into the world (under review), with the
  // magical shimmer + sound. Driven by the build picker (Journey B: clear → pick → place).
  const placeChosen = useCallback(
    async (item: CatalogueItem, taskTitle: string) => {
      if (busy) return;
      setBuildModal(null);
      setActiveTab("world");

      const clearedTask = myTasks.find((t) => t.title === taskTitle && clearedIds.has(t.id));
      if (!clearedTask) {
        flash("Couldn't find the cleared task — try again", "bad");
        return;
      }

      const occupied = new Set(placements.map((p) => `${p.x},${p.z}`));
      let spot = { x: 0, z: -1 };
      outer: for (let z = -2; z <= 3; z += 1) {
        for (let x = -3; x <= 3; x += 1) {
          if (!occupied.has(`${x},${z}`)) { spot = { x, z }; break outer; }
        }
      }

      const optId = `opt-${item.kind}`;
      const optPlacement: Placement = {
        id: optId,
        task: taskTitle,
        builder: currentUser.name,
        builderEmail: currentUser.email,
        item,
        x: spot.x,
        z: spot.z,
        state: "under_review",
        submitted: new Date().toISOString(),
      };
      setOptimisticPlacements((prev) => [...prev.filter((p) => p.id !== optId), optPlacement]);
      playShimmer();
      setPlacingShimmer(true);
      setTimeout(() => setPlacingShimmer(false), 1600);

      setBusy(true);
      try {
        await placeFn.start({
          task_id: clearedTask.id,
          component: item.kind,
          world_x: spot.x,
          world_z: spot.z,
        });
        await refreshTasks();
        flash(`Placed a ${item.label.toLowerCase()} — pending review`, "good");
      } catch {
        flash("Couldn't place the build — try again", "bad");
      } finally {
        setOptimisticPlacements((prev) => prev.filter((p) => p.id !== optId));
        setBusy(false);
      }
    },
    [busy, placements, placeFn, refreshTasks, flash, playShimmer, myTasks, clearedIds, currentUser],
  );

  // Open the build picker for a task (from the World card's CTA).
  const openBuildPicker = useCallback((task: { title: string; tier: Tier }) => {
    setBuildModal({ title: task.title, tier: task.tier });
  }, []);

  // Mark a task done: optimistic local clear + call clear_task on the pod.
  const markTaskDone = useCallback(async (task: MockTask) => {
    setClearedIds((s) => new Set(s).add(task.id));
    setBuildModal({ title: task.title, tier: task.tier });
    try {
      await clearTaskFn.start({ task_id: task.id });
      await refreshTasks();
    } catch {
      // clearedIds already set; UI remains cleared even if network fails
    }
  }, [clearTaskFn, refreshTasks]);

  // Manager assigns a new task via the modal.
  const handleAssignTask = useCallback(async (
    title: string, assigneeEmail: string, points: Tier, due: string,
  ) => {
    setAssignModalOpen(false);
    if (!activeSprint) {
      flash("No active sprint — can't assign task", "bad");
      return;
    }
    try {
      await assignTaskFn.start({
        title,
        assignee_email: assigneeEmail,
        points,
        source: "slack",
        sprint_id: activeSprint.id,
        due,
      });
      await refreshTasks();
      const memberName = nameForEmail(assigneeEmail, teamMembers);
      flash(`Assigned to ${memberName}`, "good");
    } catch {
      flash("Couldn't assign task — try again", "bad");
    }
  }, [assignTaskFn, refreshTasks, flash, activeSprint, teamMembers]);

const NAV_TABS: AppTab[] = ["world", "tasks", "review"];
  const tabLabel: Record<AppTab, string> = {
    world: "World", tasks: "Tasks", review: "Review", all: "All",
    catalog: "Catalog", kits: "Kits", stats: "Stats",
    roadmap: "Roadmap", demos: "Demos", quartermaster: "QM",
  };

  if (qmRoute) return <QuartermasterPage />;

  return (
    <main className={cinematic ? "app-shell cinematic-on" : "app-shell"} onClick={playClick}>
      <CinematicDirector
        active={cinematic}
        script={activeScript}
        stageRef={stageRef}
        controls={cine}
        playSfx={playSfx}
        onExit={() => {
          setCinematic(false);
          setSelectedId(null);
          setActiveTab("demos");
        }}
      />

      {activeTab === "roadmap" && <VisualProductDoc />}

      {/* ── DEMOS ─────────────────────────────────────── */}
      {activeTab === "demos" && (
        <section className="demo-library" aria-label="Cinematic walkthroughs">
          <div className="demo-lib-head">
            <div>
              <h2>Cinematic walkthroughs</h2>
              <p>Scripted tours of the real UI — captions, camera, and sound. Pick one to play.</p>
            </div>
            <span className="demo-lib-count">{DEMOS.length} demo{DEMOS.length === 1 ? "" : "s"}</span>
          </div>
          <div className="demo-grid">
            {DEMOS.map((demo) => (
              <button
                key={demo.id}
                type="button"
                className="demo-card"
                onClick={() => {
                  setActiveScript(demo);
                  setSelectedId(null);
                  setActiveTab("world");
                  setCinematic(true);
                }}
              >
                <div className="demo-card-icon">
                  <Film size={22} />
                </div>
                <div className="demo-card-body">
                  <div className="demo-card-top">
                    {demo.tag && <span className="demo-card-tag">{demo.tag}</span>}
                    <span className="demo-card-dur">
                      <Clock size={13} /> {Math.round(demo.duration)}s
                    </span>
                  </div>
                  <strong>{demo.title}</strong>
                  <p>{demo.blurb}</p>
                </div>
                <span className="demo-card-play">
                  <Play size={16} /> Play
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      <section
        className={`world-stage${placingShimmer ? " world-stage--shimmer" : ""}`}
        aria-label="3D task arcade world"
        ref={stageRef}
      >
        <Canvas dpr={[1, 1.8]} shadows gl={{ antialias: true }}>
          <World
            zoom={worldZoom}
            placements={view}
            onHover={handleHover}
            selectedId={selectedId}
            selectedPos={selectedPos}
          />
        </Canvas>
      </section>

      {/* Floating hover receipt (DOM overlay — pointer-events:none, never steals canvas events) */}
      {hoveredPlacement && hovered && (
        <HoverReceipt placement={hoveredPlacement} x={hovered.x} y={hovered.y} />
      )}

      {toast && (
        <div className={`toast toast--${toast.tone}`} role="status">
          {toast.tone === "good" ? <Sparkles size={17} /> : <X size={17} />}
          <span>{toast.text}</span>
        </div>
      )}

      {/* Build picker — opens when a task is cleared (Journey B: clear → pick → place) */}
      {buildModal && (
        <BuildPickerModal
          title={buildModal.title}
          tier={buildModal.tier}
          catalogue={catalogue}
          onPick={(item) => placeChosen(item, buildModal.title)}
          onClose={() => setBuildModal(null)}
        />
      )}
      {assignModalOpen && (
        <AssignTaskModal
          teamMembers={teamMembers}
          activeSprintId={activeSprint?.id ?? ""}
          onSubmit={handleAssignTask}
          onClose={() => setAssignModalOpen(false)}
        />
      )}

      <header className="app-header">
        <button className="brand-pill" type="button" onClick={() => setActiveTab("world")}>
          <Castle size={22} />
          Task Arcade
        </button>
        <nav className="app-tabs" aria-label="Desk sections">
          {NAV_TABS.map((tab) => (
            <button
              key={tab}
              className={activeTab === tab ? "tab active" : "tab"}
              type="button"
              onClick={() => setActiveTab(tab)}
            >
              {tabLabel[tab]}
              {tab === "review" && pendingCount > 0 && <i className="tab-badge">{pendingCount}</i>}
            </button>
          ))}
          <a href="#/quartermaster" className="tab tab--qm-link" type="button">
            <Sparkles size={12} /> QM
          </a>
        </nav>
        <div className="header-right">
          <div className="user-pill">
            <Avatar name={currentUser.name} color={colorForBuilder(currentUser.email, teamMembers)} email={currentUser.email} size={24} />
            <span className="user-pill-name">{currentUser.name}</span>
            <span className={`role-chip role-chip--${currentUser.role}`}>
              {currentUser.role === "manager" ? "Manager" : currentUser.role === "viewer" ? "Viewer" : "Member"}
            </span>
          </div>
          <button
            className="cine-launch"
            type="button"
            title="Browse cinematic walkthroughs"
            onClick={() => setActiveTab("demos")}
          >
            <Film size={16} /> Demos
          </button>
          <div className="world-count">
            <Layers size={16} />
            <span>{isLoading && view.length === 0 ? "Loading…" : `${placed} placed`}</span>
          </div>
        </div>
      </header>

      {/* ── WORLD ─────────────────────────────────────── */}
      {activeTab === "world" && (
        <>
          <aside className="sprint-panel" aria-label="Team sprint">
            <div className="sprint-head">
              <span className="sprint-eyebrow">Current sprint</span>
              <h1>{currentSprint?.name ?? "Loading…"}</h1>
            </div>
            <div className="sprint-goal">
              <strong>{done.toLocaleString()}</strong>
              <span> / {sprintGoal.toLocaleString()} pts goal</span>
            </div>
            <div className="sprint-bar">
              <i style={{ width: `${pct}%` }} />
            </div>
            <div className="sprint-bar-foot">
              <span>{pct}% to goal</span>
              <span>{pendingCount} pending review</span>
            </div>
            <div className="roster-head">
              <Users size={15} /> Team progress
            </div>
            <ul className="roster">
              {ranked.map((m, i) => {
                const memberRole = teamMembers.find((r) => r.email === m.email)?.role ?? "member";
                return (
                  <li key={m.email} className="roster-row">
                    <Avatar name={m.name} color={m.color} email={m.email} />
                    <span className="roster-name">{m.name}</span>
                    {memberRole === "manager" && (
                      <span className="role-chip role-chip--manager role-chip--xs">Mgr</span>
                    )}
                    {i === 0 && m.points > 0 && <Crown size={15} className="roster-crown" />}
                    <span className="roster-points">{m.points} pts</span>
                  </li>
                );
              })}
            </ul>
            <button className="leaderboard-link" type="button" onClick={() => setActiveTab("stats")}>
              <Trophy size={15} /> View full leaderboard
            </button>
          </aside>

          <aside className="task-card" aria-label="Your task">
            {myPendingBuild ? (
              <>
                <div className="task-card__eyebrow"><ClipboardList size={16} /> Your build · pending review</div>
                <h2>{myPendingBuild.task}</h2>
                <div className="task-pick">
                  <span className="task-pick__icon" style={{ background: `${TIER_COLOR[myPendingBuild.item.tier]}22`, color: TIER_COLOR[myPendingBuild.item.tier] }}>
                    <KindIcon kind={myPendingBuild.item.kind} />
                  </span>
                  <div>
                    <strong>{myPendingBuild.item.label}</strong>
                    <em>Build component · {TIER_LABEL[myPendingBuild.item.tier]}</em>
                  </div>
                  <TierBadge tier={myPendingBuild.item.tier} />
                </div>
                <div className="task-meta"><span><Clock size={15} /> Awaiting manager approval</span></div>
              </>
            ) : myHeroTask ? (
              <>
                <div className="task-card__eyebrow"><ClipboardList size={16} /> Your task · ready to build</div>
                <h2>{myHeroTask.title}</h2>
                <div className="task-pick">
                  <span className="task-pick__icon" style={{ background: `${TIER_COLOR[myHeroTask.tier]}22`, color: TIER_COLOR[myHeroTask.tier] }}>
                    <Trophy size={18} />
                  </span>
                  <div>
                    <strong>{myHeroTask.tier}-pt milestone</strong>
                    <em>Clear it to unlock {catalogue.filter(c => c.tier === myHeroTask.tier).length} builds</em>
                  </div>
                  <TierBadge tier={myHeroTask.tier} />
                </div>
                <div className="task-meta"><span><Clock size={15} /> {myHeroTask.tier} pts ready to spend</span></div>
                {!isViewer && (
                  <button className="place-button" type="button" onClick={() => openBuildPicker(myHeroTask)} disabled={busy}>
                    <Hammer size={18} /> Choose your build
                  </button>
                )}
              </>
            ) : (
              <>
                <div className="task-card__eyebrow"><ClipboardList size={16} /> No tasks assigned</div>
                <h2>Nothing to build yet</h2>
                <div className="task-meta"><span><Clock size={15} /> Ask your manager to assign a task</span></div>
              </>
            )}
          </aside>

          <div className="zoom-controls" aria-label="Camera controls">
            <button type="button" aria-label="Zoom out" onClick={() => setWorldZoom((z) => Math.max(38, z - 10))}>
              <ZoomOut size={22} />
            </button>
            <span>3D</span>
            <button type="button" aria-label="Zoom in" onClick={() => setWorldZoom((z) => Math.min(110, z + 10))}>
              <ZoomIn size={22} />
            </button>
          </div>
          <div className="hint">Hover a build to see its receipt · drag to orbit</div>
        </>
      )}

      {/* ── TASKS (Todoist-style) ────────────────────── */}
      {activeTab === "tasks" && (
        <aside className="tab-panel tasks-panel" aria-label="Task list">
          <div className="tasks-header">
            <div className="panel-header" style={{ paddingBottom: 0 }}>
              <span>Tasks</span>
              <span className={`role-chip role-chip--${currentUser.role}`}>
                {currentUser.role === "manager" ? "Manager view" : currentUser.role === "viewer" ? "Viewer" : "Member view"}
              </span>
            </div>
            <div className="tasks-toggle">
              <button className={taskView === "my" ? "active" : ""} onClick={() => setTaskView("my")}>
                My tasks
              </button>
              <button className={taskView === "team" ? "active" : ""} onClick={() => setTaskView("team")}>
                Team
              </button>
            </div>
          </div>

          {/* Manager-only: assign new task CTA */}
          {isManager && taskView === "team" && (
            <button className="assign-task-btn" type="button" onClick={() => setAssignModalOpen(true)}>
              <Plus size={14} /> Assign task to teammate
            </button>
          )}

          <ul className="task-list">
            {(taskView === "my" ? myTasks : teamTasks).map((t) => {
              const cleared = clearedIds.has(t.id);
              const effStatus: TaskStatus = cleared ? "cleared" : t.status;
              const canClear = !isViewer && taskView === "my" && t.status === "assigned";
              const assigneeName = nameForEmail(t.assignee, teamMembers);
              return (
                <li key={t.id} className="task-row">
                  <TaskStatusDot status={effStatus} />
                  <div className="task-row-body">
                    <div className="task-row-top">
                      <span className={`task-row-title ${effStatus === "cleared" ? "is-cleared" : ""}`}>{t.title}</span>
                      <TierBadge tier={t.tier} />
                    </div>
                    <div className="task-row-meta">
                      {taskView === "team" && (
                        <span className="task-assignee">
                          <Avatar name={assigneeName} color={colorForBuilder(t.assignee, teamMembers)} email={t.assignee} size={18} />
                          {assigneeName}
                        </span>
                      )}
                      <span className="task-due">{t.due}</span>
                    </div>
                  </div>
                  {canClear &&
                    (cleared ? (
                      <span className="task-done-chip">
                        <Check size={13} /> Cleared
                      </span>
                    ) : (
                      <button className="task-done-btn" type="button" onClick={() => markTaskDone(t)}>
                        <Check size={14} /> Mark done
                      </button>
                    ))}
                </li>
              );
            })}
          </ul>

          {/* View all — opens the full task list (wired later) */}
          <button
            className="view-all-link"
            type="button"
            onClick={() => flash("Full task list — coming soon", "good")}
          >
            View all tasks →
          </button>

          {(taskView === "my" ? myTasks : teamTasks).length === 0 && (
            <div className="review-empty">
              <CheckCircle2 size={28} />
              <strong>All done</strong>
              <span>No tasks in this view.</span>
            </div>
          )}
        </aside>
      )}

      {/* ── REVIEW (manager view) ────────────────────── */}
      {activeTab === "review" && (
        <aside className="tab-panel review-panel" aria-label="Review queue">
          <div className="panel-header">
            <span>Pending review</span>
            <strong>Counts toward streak</strong>
          </div>
          {pendingCount === 0 ? (
            <div className="review-empty">
              <CheckCircle2 size={30} />
              <strong>All caught up</strong>
              <span>Every build has been reviewed.</span>
            </div>
          ) : (
            <ul className="review-list">
              {pending.map((p) => (
                <li
                  key={p.id}
                  className={selectedId === p.id ? "review-row is-selected" : "review-row"}
                  onMouseEnter={() => setSelectedId(p.id)}
                  onMouseLeave={() => setSelectedId((s) => (s === p.id ? null : s))}
                >
                  <Avatar name={p.builder} color={colorForBuilder(p.builderEmail, teamMembers)} email={p.builderEmail} />
                  <div className="review-meta">
                    <strong>{p.builder}</strong>
                    <small>
                      {p.task} · {p.item.label}
                    </small>
                  </div>
                  <TierBadge tier={p.item.tier} />
                  <div className="review-actions">
                    {isManager ? (
                      <>
                        <button
                          className="rev-approve"
                          type="button"
                          disabled={busy}
                          onClick={() => decide(p.id, "approve")}
                          aria-label="Approve"
                        >
                          <Check size={16} />
                        </button>
                        <button
                          className="rev-reject"
                          type="button"
                          disabled={busy}
                          onClick={() => decide(p.id, "reject")}
                          aria-label="Reject"
                        >
                          <X size={16} />
                        </button>
                      </>
                    ) : (
                      <span className="review-viewer-note">Manager reviews</span>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <p className="phase-note">
            <Sparkles size={14} /> Hover to spotlight · approve grows it, reject demolishes it.
          </p>
          <button className="view-all-link" type="button" onClick={() => setActiveTab("all")}>
            View all activity →
          </button>
        </aside>
      )}

      {/* ── ALL ACTIVITY ─────────────────────────────── */}
      {activeTab === "all" && (
        <aside className="tab-panel all-panel" aria-label="All activity">
          <div className="all-panel-header">
            <button className="back-btn" type="button" onClick={() => setActiveTab("review")}>
              <ChevronLeft size={16} /> Review
            </button>
            <div className="panel-header" style={{ flex: 1, paddingBottom: 0 }}>
              <span>All activity</span>
              <strong>{view.length} total</strong>
            </div>
          </div>

          {pending.length > 0 && (
            <section className="all-group">
              <div className="all-group-head">
                <span className="all-dot all-dot--pending" />
                Pending review <em>{pending.length}</em>
              </div>
              <ul className="task-list">
                {pending.map((p) => (
                  <li key={p.id} className="task-row task-row--review">
                    <span className="task-dot task-dot--pending" />
                    <div className="task-row-body">
                      <div className="task-row-top">
                        <span className="task-row-title">{p.task}</span>
                        <TierBadge tier={p.item.tier} />
                      </div>
                      <div className="task-row-meta">
                        <span className="task-assignee">
                          <Avatar name={p.builder} color={colorForBuilder(p.builderEmail, teamMembers)} email={p.builderEmail} size={18} />
                          {p.builder} · {p.item.label}
                        </span>
                        <span className="task-due">{p.submitted}</span>
                      </div>
                    </div>
                    <div className="review-actions">
                      {isManager ? (
                        <>
                          <button
                            className="rev-approve"
                            type="button"
                            disabled={busy}
                            onClick={() => decide(p.id, "approve")}
                            aria-label="Approve"
                          >
                            <Check size={15} />
                          </button>
                          <button
                            className="rev-reject"
                            type="button"
                            disabled={busy}
                            onClick={() => decide(p.id, "reject")}
                            aria-label="Reject"
                          >
                            <X size={15} />
                          </button>
                        </>
                      ) : (
                        <span className="review-viewer-note">Manager reviews</span>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {established.length > 0 && (
            <section className="all-group">
              <div className="all-group-head">
                <span className="all-dot all-dot--established" />
                Established <em>{established.length}</em>
              </div>
              <ul className="task-list">
                {established.map((p) => (
                  <li key={p.id} className="task-row">
                    <span className="task-dot task-dot--cleared">
                      <Check size={10} />
                    </span>
                    <div className="task-row-body">
                      <div className="task-row-top">
                        <span className="task-row-title is-cleared">{p.task}</span>
                        <TierBadge tier={p.item.tier} />
                      </div>
                      <div className="task-row-meta">
                        <span className="task-assignee">
                          <Avatar name={p.builder} color={colorForBuilder(p.builderEmail, teamMembers)} email={p.builderEmail} size={18} />
                          {p.builder} · {p.item.label}
                        </span>
                        <span className="task-due">{p.submitted}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {demolished.length > 0 && (
            <section className="all-group">
              <div className="all-group-head">
                <span className="all-dot all-dot--demolished" />
                Demolished <em>{demolished.length}</em>
              </div>
              <ul className="task-list">
                {demolished.map((p) => (
                  <li key={p.id} className="task-row task-row--demolished">
                    <span className="task-dot task-dot--demolished" />
                    <div className="task-row-body">
                      <div className="task-row-top">
                        <span className="task-row-title">{p.task}</span>
                        <TierBadge tier={p.item.tier} />
                      </div>
                      <div className="task-row-meta">
                        <span className="task-assignee">
                          <Avatar name={p.builder} color={colorForBuilder(p.builderEmail, teamMembers)} email={p.builderEmail} size={18} />
                          {p.builder} · {p.item.label}
                        </span>
                        <span className="task-due">{p.submitted}</span>
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {view.length === 0 && (
            <div className="review-empty" style={{ marginTop: 24 }}>
              <CheckCircle2 size={28} />
              <strong>Nothing yet</strong>
              <span>Place your first build to see activity here.</span>
            </div>
          )}
        </aside>
      )}

      {/* ── CATALOG ───────────────────────────────────── */}
      {activeTab === "catalog" && (
        <aside className="tab-panel" aria-label="Build catalogue">
          <div className="panel-header">
            <span>Build catalogue</span>
            <strong>By point tier</strong>
          </div>
          <div className="catalog-tiers">
            {catalogueByTier.map(({ tier, items }) => (
              <section className="catalog-tier" key={tier}>
                <header>
                  <TierBadge tier={tier} />
                  <strong>{tier} pts</strong>
                  <span>{TIER_LABEL[tier]}</span>
                </header>
                <div className="catalog-items">
                  {items.map((c) => (
                    <div className="catalog-chip" key={c.kind}>
                      <KindIcon kind={c.kind} />
                      {c.label}
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </aside>
      )}

      {/* ── STATS ─────────────────────────────────────── */}
      {activeTab === "stats" && (
        <aside className="tab-panel" aria-label="Team stats">
          <div className="panel-header">
            <span>Team stats</span>
            <strong>{done.toLocaleString()} pts</strong>
          </div>
          <div className="stat-grid">
            <div>
              <strong>{placed}</strong>
              <span>placed</span>
            </div>
            <div>
              <strong>{pendingCount}</strong>
              <span>pending</span>
            </div>
            <div>
              <strong>{members.length}</strong>
              <span>builders</span>
            </div>
          </div>
          <div className="roster-head">
            <Trophy size={15} /> Leaderboard
          </div>
          <ul className="roster">
            {ranked.map((m, i) => (
              <li key={m.email} className="roster-row">
                <span className="roster-rank">{i + 1}</span>
                <Avatar name={m.name} color={m.color} email={m.email} />
                <span className="roster-name">{m.name}</span>
                <span className="roster-points">{m.points} pts</span>
              </li>
            ))}
          </ul>
        </aside>
      )}

      {/* ── KITS ──────────────────────────────────────── */}
      {activeTab === "kits" && (
        <aside className="tab-panel" aria-label="World kits">
          <div className="panel-header">
            <span>World kits</span>
            <strong>Art themes</strong>
          </div>
          <div className="kit-list">
            {[
              { name: "Fantasy town", note: "Cottages, mills, stalls", swatch: "#8fcf72" },
              { name: "Urban city", note: "Manors, towers, planters", swatch: "#9bb7d8" },
              { name: "Pirate bay", note: "Ships, gates, watchtowers", swatch: "#e0b070" },
              { name: "Motor pool", note: "Carts, vans, delivery", swatch: "#d68b9a" },
            ].map((k) => (
              <section className="kit-row" key={k.name}>
                <div>
                  <strong>{k.name}</strong>
                  <span>{k.note}</span>
                </div>
                <span className="kit-swatch" style={{ background: k.swatch }} />
              </section>
            ))}
          </div>
        </aside>
      )}

    </main>
  );
}

PRELOAD_URLS.forEach((url) => useGLTF.preload(url));

export default App;
