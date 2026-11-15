/**
 * The rest of a band member below the CHIMERA head: a wireframe robot torso and arms, and the
 * instrument it plays, drawn with the head's own glowing beams so the two read as one machine.
 *
 * Coordinates are the head's (body cm, scaled by the body's size): the face looks along +x, y is
 * up, +z is the robot's right. Arms are two-bone IK chains reaching for targets the instrument sets
 * each frame from the notes: a strumming hand that swings on each chord, a fretting hand that
 * slides to the pitch, sticks that lift before a hit and land on it, fingers over pressed keys.
 */
import * as THREE from "three";
import { Beams } from "../ui/render/beams3d.ts";
import { V } from "../ui/colors.ts";

type P3 = [number, number, number];
export type Note = [number, number, number, number]; // start s, end s, midi, velocity

export type RigKind = "singer" | "lead" | "choir" | "beatbox" | "guitar" | "bass" | "piano" | "synth" | "strings" | "drums";

export interface RigFrame {
  t: number;
  dt: number;
  /** how loud the voice is now, 0..1 (singers) */
  level: number;
  notes: Note[];
}

const add = (a: P3, b: P3): P3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const sub = (a: P3, b: P3): P3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const mul = (a: P3, s: number): P3 => [a[0] * s, a[1] * s, a[2] * s];
const dot = (a: P3, b: P3) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a: P3, b: P3): P3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
const len = (a: P3) => Math.hypot(a[0], a[1], a[2]);
const norm = (a: P3): P3 => mul(a, 1 / (len(a) || 1));
const lerp = (a: P3, b: P3, u: number): P3 => [a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u, a[2] + (b[2] - a[2]) * u];
const smooth = (u: number) => u * u * (3 - 2 * u);
const clamp = (x: number, a: number, b: number) => Math.max(a, Math.min(b, x));

/** a frame for a flat thing: origin, in-plane u and v, normal n */
interface Frame3 {
  o: P3;
  u: P3;
  v: P3;
  n: P3;
}
const at = (f: Frame3, a: number, b: number, c = 0): P3 => add(add(add(f.o, mul(f.u, a)), mul(f.v, b)), mul(f.n, c));
function frame(o: P3, u: P3, up: P3): Frame3 {
  const uu = norm(u);
  const n = norm(cross(uu, up));
  const v = cross(n, uu);
  return { o, u: uu, v, n };
}

export class Rig {
  readonly beams = new Beams();
  private readonly flat = new Float32Array(3 * 256);
  private k = 1;
  private y0 = -4.6;
  private strum = 0;
  private strumDir = 1;
  private lastChord = -1;
  private bow = 0;
  private fretZ = 0;
  private handsKeys: [number, number] = [0, 0];
  /** the instrument alone, no robot holding it (the studio's look); arms and torso are skipped */
  bodyless = true;
  /** what was drawn last frame, for framing the camera on it */
  readonly lo: P3 = [Infinity, Infinity, Infinity];
  readonly hi: P3 = [-Infinity, -Infinity, -Infinity];

  constructor(readonly kind: RigKind, private readonly color: string) {}

  /** the camera on last frame's drawing: its middle, and the distance that fits it in the pane */
  frameOnDrawing(aspect: number, facing: 1 | -1, az = 1.2, el = 0.28, fovDeg = 24): { target: THREE.Vector3; dir: THREE.Vector3; dist: number } {
    const c = new THREE.Vector3((this.lo[0] + this.hi[0]) / 2, (this.lo[1] + this.hi[1]) / 2, (this.lo[2] + this.hi[2]) / 2);
    const size = new THREE.Vector3(this.hi[0] - this.lo[0], this.hi[1] - this.lo[1], this.hi[2] - this.lo[2]);
    const dir = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), facing * Math.cos(az) * Math.cos(el));
    // the extent seen across and up the screen, roughly
    const across = Math.hypot(size.z * Math.sin(az), size.x * Math.cos(az));
    const up = size.y * Math.cos(el) + Math.max(size.x, size.z) * Math.sin(el) * 0.5;
    const half = Math.tan((fovDeg * Math.PI) / 360);
    const dist = 0.98 * Math.max(up / 2 / half, across / 2 / (half * aspect)) + Math.max(size.x, size.z) * 0.25;
    return { target: c, dir, dist };
  }

  /** the head's extent (from Anatomy3D.bounds) */
  fit(y0: number, k: number): void {
    this.y0 = y0;
    this.k = k;
  }

  /** where the camera looks from: the target, the direction to the camera, the distance that
   *  fits this member's height (head to instrument) in a pane of the given aspect */
  framing(y1: number, facing: 1 | -1, aspect: number, fovDeg = 24): { target: THREE.Vector3; dir: THREE.Vector3; dist: number } {
    const k = this.k, y0 = this.y0;
    const ext: Record<RigKind, [number, number, number, number]> = {
      // bottom, top (head cm), forward shift of the target, azimuth from the side towards the front
      lead: [y0 - 24 * k, y1 + 1 * k, 3 * k, 0.72],
      singer: [y0 - 28 * k, y1 + 7 * k, 2 * k, 0.8],
      choir: [y0 - 28 * k, y1 + 7 * k, 2 * k, 0.8],
      beatbox: [y0 - 28 * k, y1 + 7 * k, 3 * k, 0.8],
      guitar: [y0 - 56 * k, y1 + 6 * k, 9 * k, 1.05],
      bass: [y0 - 58 * k, y1 + 6 * k, 9 * k, 1.05],
      piano: [y0 - 52 * k, y1 + 6 * k, 10 * k, 1.0],
      synth: [y0 - 52 * k, y1 + 6 * k, 10 * k, 1.0],
      strings: [y0 - 42 * k, y1 + 6 * k, 8 * k, 0.95],
      drums: [y0 - 78 * k, y1 + 6 * k, 14 * k, 1.1],
    };
    const [lo, hi, fwd, az] = ext[this.kind];
    const half = Math.tan((fovDeg * Math.PI) / 360);
    const hgt = (hi - lo) / 2;
    // the width it needs: about as wide as tall for players, narrower for busts
    const wide = this.kind === "drums" || this.kind === "piano" || this.kind === "synth" ? 1.05 : this.kind === "guitar" || this.kind === "bass" ? 0.95 : 0.75;
    const dist = 1.08 * Math.max(hgt / half, (hgt * wide) / (half * aspect));
    const el = 0.12;
    const dir = new THREE.Vector3(Math.sin(az) * Math.cos(el), Math.sin(el), facing * Math.cos(az) * Math.cos(el));
    return { target: new THREE.Vector3(fwd, (lo + hi) / 2, 0), dir, dist };
  }

  // ------------------------------------------------------------------ drawing primitives
  private P(pts: P3[], color: string, w: number, alpha = 1, close = false, hot = false): void {
    const n = Math.min(pts.length, 256);
    for (let i = 0; i < n; i++) {
      this.flat[3 * i] = pts[i][0];
      this.flat[3 * i + 1] = pts[i][1];
      this.flat[3 * i + 2] = pts[i][2];
      if (!this.measuring) continue;
      for (let j = 0; j < 3; j++) {
        if (pts[i][j] < this.lo[j]) this.lo[j] = pts[i][j];
        if (pts[i][j] > this.hi[j]) this.hi[j] = pts[i][j];
      }
    }
    this.beams.line(this.flat, n, color, w, { alpha, close, hot });
  }

  private ring(c: P3, a: P3, b: P3, ra: number, rb: number, color: string, w: number, alpha = 1, n = 28, hot = false): void {
    const pts: P3[] = [];
    for (let i = 0; i < n; i++) {
      const t = (i / n) * Math.PI * 2;
      pts.push(add(c, add(mul(a, Math.cos(t) * ra), mul(b, Math.sin(t) * rb))));
    }
    this.P(pts, color, w, alpha, true, hot);
  }

  private arc(c: P3, a: P3, b: P3, r: number, t0: number, t1: number, color: string, w: number, alpha = 1, n = 16): void {
    const pts: P3[] = [];
    for (let i = 0; i <= n; i++) {
      const t = t0 + ((t1 - t0) * i) / n;
      pts.push(add(c, add(mul(a, Math.cos(t) * r), mul(b, Math.sin(t) * r))));
    }
    this.P(pts, color, w, alpha);
  }

  /** a tapered tube: rings at both ends and the middle, lines along it */
  private tube(p0: P3, p1: P3, r0: number, r1: number, color: string, w = 1.2, alpha = 0.9, sides = 6, rings = 3): void {
    const d = norm(sub(p1, p0));
    const a = norm(cross(d, Math.abs(d[1]) > 0.9 ? [1, 0, 0] : [0, 1, 0]));
    const b = cross(d, a);
    for (let i = 0; i < rings; i++) {
      const u = rings === 1 ? 0 : i / (rings - 1);
      this.ring(lerp(p0, p1, u), a, b, r0 + (r1 - r0) * u, r0 + (r1 - r0) * u, color, w * (i === 0 || i === rings - 1 ? 1 : 0.7), alpha * (i === 0 || i === rings - 1 ? 1 : 0.6), 16);
    }
    for (let s = 0; s < sides; s++) {
      const t = (s / sides) * Math.PI * 2;
      const off = (r: number) => add(mul(a, Math.cos(t) * r), mul(b, Math.sin(t) * r));
      this.P([add(p0, off(r0)), add(p1, off(r1))], color, w * 0.8, alpha * 0.7);
    }
  }

  private ball(c: P3, r: number, color: string, w = 1, alpha = 0.9, hot = false): void {
    this.ring(c, [1, 0, 0], [0, 1, 0], r, r, color, w, alpha, 18, hot);
    this.ring(c, [0, 0, 1], [0, 1, 0], r, r, color, w, alpha * 0.8, 18);
    this.ring(c, [1, 0, 0], [0, 0, 1], r, r, color, w, alpha * 0.6, 18);
  }

  private box(f: Frame3, hu: number, hv: number, hn: number, color: string, w = 1, alpha = 0.9, hot = false): void {
    const c = (a: number, b: number, d: number) => at(f, a * hu, b * hv, d * hn);
    for (const d of [-1, 1]) this.P([c(-1, -1, d), c(1, -1, d), c(1, 1, d), c(-1, 1, d)], color, w, alpha, true, hot);
    for (const a of [-1, 1]) for (const b of [-1, 1]) this.P([c(a, b, -1), c(a, b, 1)], color, w, alpha * 0.8);
  }

  /** two-bone IK: the elbow for a hand at `t`, bending towards `pole` */
  private ik(s: P3, t: P3, a: number, b: number, pole: P3): [P3, P3] {
    let d = sub(t, s);
    let dl = len(d);
    const reach = (a + b) * 0.999;
    if (dl > reach) {
      d = mul(d, reach / dl);
      dl = reach;
      t = add(s, d);
    }
    const dir = norm(d);
    const cosA = clamp((a * a + dl * dl - b * b) / (2 * a * dl), -1, 1);
    const sinA = Math.sqrt(1 - cosA * cosA);
    let p = sub(pole, mul(dir, dot(pole, dir)));
    p = norm(p);
    const elbow = add(add(s, mul(dir, a * cosA)), mul(p, a * sinA));
    return [elbow, t];
  }

  private measuring = true;

  private arm(side: 1 | -1, hand: P3, grip: P3 | null, color: string): void {
    if (this.bodyless) return;
    const k = this.k;
    const sh: P3 = [-2 * k, this.y0 - 6.5 * k, side * 18.5 * k];
    const [el, h] = this.ik(sh, hand, 27 * k, 25 * k, [-0.6, -1, side * 0.9]);
    this.ball(sh, 4.2 * k, color, 1.2, 0.9);
    this.tube(sh, el, 3.4 * k, 2.8 * k, color, 1.2, 0.85);
    this.ball(el, 2.6 * k, color, 1.1, 0.9);
    this.tube(el, h, 2.6 * k, 2.0 * k, color, 1.1, 0.85);
    // the hand: a palm plate and four jointed fingers curling towards the grip
    const fw = norm(sub(h, el));
    const across = norm(cross(fw, [0, 1, 0]));
    const palm = add(h, mul(fw, 2.6 * k));
    const pf = frame(palm, fw, cross(across, fw));
    this.box(pf, 2.4 * k, 0.6 * k, 2.2 * k, color, 1, 0.85);
    const toward = grip ? norm(sub(grip, palm)) : fw;
    for (let i = 0; i < 4; i++) {
      const base = at(pf, 2.4 * k, 0, (-1.5 + i) * 1.3 * k);
      const mid = add(base, mul(norm(add(fw, mul(toward, 0.6))), 2.2 * k));
      const tip = add(mid, mul(toward, 1.8 * k));
      this.P([base, mid, tip], color, 0.9, 0.85);
    }
    this.P([at(pf, 0.5 * k, 0, side * 2.4 * k), add(at(pf, 1.5 * k, 0, side * 3.6 * k), mul(toward, 1.6 * k))], color, 0.9, 0.85);
  }

  private torso(color: string, glow: number): void {
    if (this.bodyless) return;
    const k = this.k;
    const top = this.y0 - 2.5 * k;
    const cx = -2.2 * k;
    // a superellipse cross-section at height y: chest broad, waist narrower
    const sect = (y: number, hz: number, hx: number, alpha: number, wide = 1.1) => {
      const pts: P3[] = [];
      for (let i = 0; i < 32; i++) {
        const t = (i / 32) * Math.PI * 2;
        const c = Math.cos(t), s = Math.sin(t);
        const e = 0.6;
        pts.push([cx + Math.sign(c) * Math.abs(c) ** e * hx, y, Math.sign(s) * Math.abs(s) ** e * hz]);
      }
      this.P(pts, color, wide, alpha, true);
    };
    const rows = [
      [0, 6.5, 5.5],
      [-3, 15.5, 8.5],
      [-7, 19, 10],
      [-12, 18.5, 11],
      [-18, 16.5, 10.5],
      [-24, 13.5, 9],
      [-30, 11.5, 8],
      [-34, 12, 8.2],
      [-40, 14, 9],
    ];
    rows.forEach(([dy, hz, hx], i) => sect(top + dy * k, hz * k, hx * k, i === 0 || i === 3 || i === rows.length - 1 ? 0.95 : 0.55, i === 3 ? 1.4 : 1));
    // seams down the front and back, and the plates' edges
    for (const zz of [-1, 0, 1]) {
      this.P(rows.map(([dy, hz, hx]) => [cx + hx * k * 0.98, top + dy * k, zz * hz * k * 0.45] as P3), color, 0.9, zz === 0 ? 0.8 : 0.45);
      this.P(rows.map(([dy, hz, hx]) => [cx - hx * k * 0.98, top + dy * k, zz * hz * k * 0.45] as P3), color, 0.8, 0.35);
    }
    for (const side of [-1, 1]) this.P(rows.map(([dy, hz]) => [cx, top + dy * k, side * hz * k] as P3), color, 1, 0.7);
    // the chest core: a ring that glows with the sound
    const core: P3 = [cx + 10.8 * k, top - 13 * k, 0];
    const fr = frame(core, [0, 0, 1], [0, 1, 0]);
    this.ring(core, fr.u, fr.v, 4.2 * k, 4.2 * k, color, 1.4, 0.9, 28);
    this.ring(core, fr.u, fr.v, (2 + 1.6 * glow) * k, (2 + 1.6 * glow) * k, V.white, 1.8, 0.35 + 0.65 * glow, 24, glow > 0.3);
    for (let i = 0; i < 6; i++) {
      const t = (i / 6) * Math.PI * 2;
      this.P([at(fr, Math.cos(t) * 4.6 * k, Math.sin(t) * 4.6 * k), at(fr, Math.cos(t) * 6 * k, Math.sin(t) * 6 * k)], color, 1, 0.6);
    }
    // collar and shoulder plates
    this.ring([cx, top + 0.6 * k, 0], [1, 0, 0], [0, 0, 1], 7.5 * k, 7.5 * k, color, 1.3, 0.9);
    for (const side of [-1, 1] as const) {
      const sh: P3 = [-2 * k, this.y0 - 6.5 * k, side * 18.5 * k];
      this.arc(sh, [0, 0, side], [0, 1, 0], 7 * k, -0.4, 1.9, color, 1.2, 0.8);
      this.arc(add(sh, [3 * k, 0, 0]), [0, 0, side], [0, 1, 0], 6.4 * k, -0.2, 1.7, color, 1, 0.55);
      this.arc(add(sh, [-3 * k, 0, 0]), [0, 0, side], [0, 1, 0], 6.4 * k, -0.2, 1.7, color, 1, 0.45);
    }
  }

  // ------------------------------------------------------------------ one frame
  update(f: RigFrame, center: THREE.Vector3): void {
    const B = this.beams;
    B.begin();
    for (let j = 0; j < 3; j++) {
      this.lo[j] = Infinity;
      this.hi[j] = -Infinity;
    }
    const k = this.k;
    const col = this.color;
    const act = f.notes.filter((n) => n[0] <= f.t && f.t < n[1] + 0.03);
    const recent = (win: number) => f.notes.filter((n) => n[0] <= f.t && f.t - n[0] < win);
    const glow = this.kind === "drums" ? Math.max(0, ...recent(0.25).map((n) => (n[3] / 127) * Math.exp(-(f.t - n[0]) * 10))) : act.length ? 0.6 + 0.4 * Math.max(...act.map((n) => n[3] / 127)) : f.level;
    this.torso(col, clamp(glow, 0, 1));
    const lips: P3 = [center.x + 7 * k, center.y - 2.5 * k, 0];
    switch (this.kind) {
      case "lead":
      case "beatbox":
        if (!this.bodyless) this.microphone(lips, f.level);
        break;
      case "singer":
      case "choir":
        this.arm(1, [8 * k, this.y0 - 40 * k, 12 * k], null, col);
        this.arm(-1, [8 * k, this.y0 - 40 * k, -12 * k], null, col);
        break;
      case "guitar":
      case "bass":
        this.guitar(f, act, this.kind === "bass");
        break;
      case "piano":
      case "synth":
        this.keys(f, act);
        break;
      case "strings":
        this.violin(f, act);
        break;
      case "drums":
        this.drums(f);
        break;
    }
    B.end();
  }

  // ------------------------------------------------------------------ microphone (lead, beatbox)
  private microphone(lips: P3, level: number): void {
    const k = this.k, col = this.color;
    const head: P3 = add(lips, [4.5 * k, -4.5 * k, 1.5 * k]);
    const dir = norm([0.35, -1, 0.15]);
    const end = add(head, mul(dir, 17 * k));
    // the grille: a ball of crossing rings, the handle a tapered tube with a band
    this.ball(head, 3 * k, col, 1.1, 0.95, level > 0.3);
    for (let i = 1; i < 4; i++) this.ring(head, [1, 0, 0], [0, 0, 1], 3 * k * Math.sin((i * Math.PI) / 4), 3 * k * Math.sin((i * Math.PI) / 4), col, 0.8, 0.55, 16);
    this.tube(add(head, mul(dir, 2.8 * k)), end, 1.9 * k, 1.3 * k, col, 1.1, 0.9, 8, 4);
    this.ring(add(head, mul(dir, 6 * k)), norm(cross(dir, [0, 0, 1])), [0, 0, 1], 2.1 * k, 2.1 * k, V.white, 1, 0.5, 16);
    this.arm(1, add(head, mul(dir, 9 * k)), head, col);
    this.arm(-1, [10 * k, this.y0 - 38 * k, -10 * k], null, col);
  }

  // ------------------------------------------------------------------ guitar and bass
  private guitar(f: RigFrame, act: Note[], bass: boolean): void {
    const k = this.k, col = this.color;
    const nStr = bass ? 4 : 6;
    // the guitar's plane faces forward (+x); its neck runs up to the robot's left (−z)
    const body: P3 = [13 * k, this.y0 - 31 * k, 5 * k];
    const neckDir = norm([0.12, 0.42, -1]);
    // the face of the guitar towards the audience: +x made square to the neck
    const fwd: P3 = [1, 0, 0.15];
    const n = norm(sub(fwd, mul(neckDir, dot(fwd, neckDir))));
    const G: Frame3 = { o: body, u: neckDir, v: norm(cross(n, neckDir)), n };
    const L = (bass ? 1.15 : 1) * k;
    // body outline: two lobes and a waist, front and back faces joined at the rim
    const outline = (depth: number): P3[] => {
      const pts: P3[] = [];
      for (let i = 0; i < 48; i++) {
        const t = (i / 48) * Math.PI * 2;
        const x = Math.cos(t) * 13 * L;
        const lobe = x < 0 ? 1 : 0.82;
        const waist = 1 - 0.2 * Math.exp(-((x / (13 * L) - 0.15) ** 2) * 10);
        pts.push(at(G, x, Math.sin(t) * 10 * L * lobe * waist, depth));
      }
      return pts;
    };
    const front = outline(2.2 * k), back = outline(-2.2 * k);
    this.P(front, col, 1.6, 1, true);
    this.P(back, col, 1.1, 0.55, true);
    for (let i = 0; i < 48; i += 4) this.P([front[i], back[i]], col, 0.8, 0.45);
    // sound hole (or pickups on a bass), bridge, pickguard
    if (!bass) {
      for (const r of [3.2, 3.9]) this.ring(at(G, 4 * L, 0, 2.2 * k), G.u, G.v, r * k, r * k, col, 1, 0.8, 28);
    } else {
      this.box(frame(at(G, 3 * L, 0, 2.4 * k), G.u, G.v), 1.2 * k, 4 * k, 0.3 * k, col, 1, 0.8);
      this.box(frame(at(G, -3 * L, 0, 2.4 * k), G.u, G.v), 1.2 * k, 4 * k, 0.3 * k, col, 1, 0.8);
    }
    this.box(frame(at(G, -7 * L, 0, 2.4 * k), G.u, G.v), 0.8 * k, 4.5 * k, 0.4 * k, col, 1.2, 0.9);
    // the neck, frets and headstock with tuning pegs
    const n0 = 10 * L, n1 = (bass ? 62 : 52) * k;
    const nw = (bass ? 2.2 : 2.6) * k;
    this.P([at(G, n0, -nw, 2.4 * k), at(G, n1, -nw * 0.8, 2.4 * k), at(G, n1, nw * 0.8, 2.4 * k), at(G, n0, nw, 2.4 * k)], col, 1.3, 0.95);
    this.P([at(G, n0, -nw, 0.6 * k), at(G, n1, -nw * 0.8, 0.6 * k)], col, 0.8, 0.5);
    this.P([at(G, n0, nw, 0.6 * k), at(G, n1, nw * 0.8, 0.6 * k)], col, 0.8, 0.5);
    const fretAt = (i: number) => n1 - (n1 - n0) * (1 - 2 ** (-i / 12)) * 1.75;
    for (let i = 1; i <= 15; i++) {
      const x = fretAt(i);
      if (x > n0) this.P([at(G, x, -nw * 0.9, 2.5 * k), at(G, x, nw * 0.9, 2.5 * k)], col, 0.8, 0.5);
    }
    const hs: P3[] = [at(G, n1, -nw, 2.4 * k), at(G, n1 + 9 * k, -nw * 1.7, 2.0 * k), at(G, n1 + 10 * k, nw * 1.7, 2.0 * k), at(G, n1, nw, 2.4 * k)];
    this.P(hs, col, 1.3, 0.95, true);
    for (let i = 0; i < nStr; i++) {
      const side = i < nStr / 2 ? -1 : 1;
      const x = n1 + 2 * k + (i % (nStr / 2)) * 2.6 * k;
      this.ring(at(G, x, side * nw * 1.9, 2.2 * k), G.u, G.n, 0.7 * k, 0.7 * k, col, 0.9, 0.8, 10);
    }
    // strings: a struck one shimmers and glows
    const lowest = bass ? 28 : 40;
    const span = bass ? [28, 33, 38, 43] : [40, 45, 50, 55, 59, 64];
    const ringing = new Array(nStr).fill(0);
    for (const nt of act) {
      let s = 0;
      for (let i = 0; i < nStr; i++) if (nt[2] >= span[i]) s = i;
      ringing[s] = Math.max(ringing[s], (nt[3] / 127) * Math.exp(-(f.t - nt[0]) * 3));
    }
    for (let i = 0; i < nStr; i++) {
      const y = (-0.8 + (1.6 * i) / (nStr - 1)) * nw * 0.85;
      const pts: P3[] = [];
      const amp = ringing[i] * 0.55 * k;
      for (let s = 0; s <= 20; s++) {
        const u = s / 20;
        const x = -7 * L + (n1 + 7 * L) * u;
        pts.push(at(G, x, y + amp * Math.sin(Math.PI * u) * Math.sin(f.t * 140 + i * 2.1), 2.7 * k));
      }
      this.P(pts, ringing[i] > 0.05 ? V.white : col, ringing[i] > 0.05 ? 1.2 : 0.8, 0.5 + 0.5 * Math.min(1, ringing[i] * 2), false, ringing[i] > 0.2);
    }
    // the fretting hand slides to where the notes are; the picking hand swings on each chord
    const top = act.length ? Math.max(...act.map((n) => n[2])) : lowest + 7;
    const fret = clamp(top - lowest - (bass ? 0 : 5), 0, 12);
    const goal = fretAt(fret + 0.5);
    this.fretZ += (goal - this.fretZ) * Math.min(1, f.dt * 14);
    const chordStart = act.length ? Math.max(...act.map((n) => n[0])) : -1;
    if (chordStart !== this.lastChord && chordStart >= 0) {
      this.lastChord = chordStart;
      this.strumDir *= -1;
      this.strum = 1;
    }
    this.strum = Math.max(0, this.strum - f.dt * 7);
    const sweep = (bass ? 0.4 : 1) * this.strumDir * (smooth(1 - this.strum) - 0.5) * 7 * k;
    const pick = at(G, 4 * L, sweep, 5 * k);
    this.arm(1, add(pick, mul(G.n, 1.5 * k)), at(G, 4 * L, 0, 2.4 * k), col);
    this.arm(-1, at(G, this.fretZ || goal, -nw * 1.4, 1 * k), at(G, this.fretZ || goal, 0, 2.4 * k), col);
  }

  // ------------------------------------------------------------------ piano and synth
  private keys(f: RigFrame, act: Note[]): void {
    const k = this.k, col = this.color;
    const lo = 48, hi = 84;
    const o: P3 = [17 * k, this.y0 - 34 * k, 0];
    const K: Frame3 = { o, u: [0, 0, 1], v: [1, 0, 0], n: [0, 1, 0] }; // along the keys, towards the robot, up
    const white = [0, 2, 4, 5, 7, 9, 11];
    const whites: number[] = [];
    for (let m = lo; m <= hi; m++) if (white.includes(m % 12)) whites.push(m);
    const ww = 2.3 * k, wl = 13 * k;
    const x0 = (-whites.length * ww) / 2;
    const down = new Map<number, number>();
    for (const n of act) down.set(n[2] < lo ? lo + (n[2] % 12) : n[2] > hi ? hi - 12 + (n[2] % 12) : n[2], n[3] / 127);
    // the case
    this.box(frame(at(K, 0, wl * 0.35, -2.5 * k), [0, 0, 1], [0, 1, 0]), (whites.length * ww) / 2 + 3 * k, 2.5 * k, wl * 0.9, col, 1.2, 0.85);
    this.P([at(K, x0 - 3 * k, wl * 1.2, 0), at(K, x0 - 3 * k, wl * 1.2, 6 * k), at(K, -x0 + 3 * k, wl * 1.2, 6 * k), at(K, -x0 + 3 * k, wl * 1.2, 0)], col, 1.1, 0.7);
    if (this.kind === "synth") for (let i = 0; i < 8; i++) this.ring(at(K, x0 + 4 * k + i * 4 * k, wl * 1.05, 4 * k), [0, 0, 1], [0, 1, 0], 1 * k, 1 * k, col, 0.9, 0.7, 12);
    whites.forEach((m, i) => {
      const d = down.get(m);
      const z = d ? -0.8 * k : 0;
      const a = x0 + i * ww;
      this.P([at(K, a, 0, z), at(K, a + ww, 0, z), at(K, a + ww, wl, z), at(K, a, wl, z)], d ? V.white : col, d ? 1.4 : 0.9, d ? 1 : 0.6, true, !!d);
      if (white.includes((m + 1) % 12) === false && m + 1 <= hi) {
        const db = down.get(m + 1);
        const bz = 1.2 * k + (db ? -0.8 * k : 0);
        const b0 = a + ww * 0.68, b1 = a + ww * 1.32;
        this.P([at(K, b0, wl * 0.4, bz), at(K, b1, wl * 0.4, bz), at(K, b1, wl, bz), at(K, b0, wl, bz)], db ? V.white : col, db ? 1.3 : 1, db ? 1 : 0.8, true, !!db);
      }
    });
    // hands over the lowest and highest keys sounding
    const keyX = (m: number) => {
      const idx = whites.findIndex((w) => w >= m);
      return x0 + (idx < 0 ? whites.length - 1 : idx) * ww + ww / 2;
    };
    const ms = [...down.keys()].sort((a, b) => a - b);
    const lx = ms.length ? keyX(ms[0]) : keyX(55), rx = ms.length ? keyX(ms[ms.length - 1]) : keyX(72);
    this.handsKeys[0] += (lx - this.handsKeys[0]) * Math.min(1, f.dt * 12);
    this.handsKeys[1] += (Math.max(rx, lx + 8 * k) - this.handsKeys[1]) * Math.min(1, f.dt * 12);
    const press = (on: boolean) => (on ? 1.5 * k : 4 * k);
    this.arm(-1, at(K, Math.min(this.handsKeys[0], -2 * k), wl * 0.85, press(ms.length > 0)), at(K, this.handsKeys[0], wl * 0.4, 0), col);
    this.arm(1, at(K, Math.max(this.handsKeys[1], 2 * k), wl * 0.85, press(ms.length > 1)), at(K, this.handsKeys[1], wl * 0.4, 0), col);
  }

  // ------------------------------------------------------------------ violin
  private violin(f: RigFrame, act: Note[]): void {
    const k = this.k, col = this.color;
    const chin: P3 = [3 * k, this.y0 - 8 * k, -9 * k];
    let V3: Frame3;
    if (this.bodyless) {
      // on its own: face to the audience, the neck up and to the left, the bow across the strings
      const dir = norm([0, 0.8, -0.6]);
      V3 = { o: chin, u: dir, n: [1, 0, 0], v: cross([1, 0, 0], dir) };
    } else {
      V3 = frame(chin, norm([0.85, 0.1, -0.5]), [0, 1, 0]); // under the chin, scroll forward and out
    }
    const outline: P3[] = [];
    for (let i = 0; i < 40; i++) {
      const t = (i / 40) * Math.PI * 2;
      const x = Math.cos(t) * 9 * k;
      const waist = 1 - 0.3 * Math.exp(-((x / (9 * k)) ** 2) * 9);
      outline.push(at(V3, x + 4 * k, Math.sin(t) * 5.5 * k * waist, 0));
    }
    this.P(outline, col, 1.4, 1, true);
    this.P(outline.map((p) => add(p, mul(V3.v, -1.6 * k))), col, 0.9, 0.5, true);
    for (const s of [-1, 1]) this.P([at(V3, 2 * k, s * 2 * k, 0.3 * k), at(V3, 4 * k, s * 1.6 * k, 0.3 * k), at(V3, 6 * k, s * 2.1 * k, 0.3 * k)], col, 0.9, 0.7);
    this.P([at(V3, 13 * k, -0.7 * k), at(V3, 30 * k, -0.6 * k), at(V3, 30 * k, 0.6 * k), at(V3, 13 * k, 0.7 * k)], col, 1.1, 0.9);
    this.ring(at(V3, 31.5 * k, 0), V3.u, V3.n, 1.4 * k, 1.4 * k, col, 1, 0.9, 14);
    const sounding = act.length > 0;
    for (let i = 0; i < 4; i++) this.P([at(V3, -3 * k, (-0.6 + 0.4 * i) * k, 0.4 * k), at(V3, 30 * k, (-0.45 + 0.3 * i) * k, 0.4 * k)], sounding ? V.white : col, 0.8, sounding ? 0.9 : 0.6);
    // the bow moves while a note sounds
    if (sounding) this.bow = (this.bow + f.dt * 1.4) % 2;
    const b = this.bow < 1 ? this.bow : 2 - this.bow;
    const contact = at(V3, 0, 0, 1 * k);
    const bowDir = norm(cross(V3.u, V3.n));
    const p0 = add(contact, mul(bowDir, (-24 + 20 * b) * k)), p1 = add(contact, mul(bowDir, (14 + 20 * b) * k));
    this.P([p0, p1], col, 1.3, 1, false, sounding);
    this.P([add(p0, [0, -1 * k, 0]), add(p1, [0, -1 * k, 0])], col, 0.8, 0.6);
    this.arm(1, add(p0, mul(bowDir, 2 * k)), p0, col);
    const top = act.length ? Math.max(...act.map((n) => n[2])) : 67;
    this.arm(-1, at(V3, (28 - clamp(top - 55, 0, 24) * 0.55) * k, -1.6 * k, -0.5 * k), at(V3, 24 * k, 0), col);
  }

  // ------------------------------------------------------------------ drum kit
  private drums(f: RigFrame): void {
    const k = this.k, col = this.color;
    const Y = this.y0;
    type Pad = { c: P3; r: number; tilt: P3; cym?: boolean; ids: number[]; hand: 1 | -1 };
    const pads: Pad[] = [
      { c: [19 * k, Y - 40 * k, 9 * k], r: 7 * k, tilt: norm([-0.3, 1, 0]), ids: [37, 38, 39, 40], hand: -1 },
      { c: [17 * k, Y - 33 * k, 25 * k], r: 7 * k, tilt: [0, 1, 0], cym: true, ids: [42, 44, 46], hand: 1 },
      { c: [29 * k, Y - 34 * k, -3 * k], r: 6 * k, tilt: norm([-0.5, 1, 0.1]), ids: [48, 50], hand: -1 },
      { c: [20 * k, Y - 47 * k, -20 * k], r: 8.5 * k, tilt: [0, 1, 0], ids: [41, 43, 45, 47], hand: -1 },
      { c: [31 * k, Y - 20 * k, -24 * k], r: 11 * k, tilt: norm([-0.4, 1, -0.3]), cym: true, ids: [49, 52, 55, 57], hand: -1 },
      { c: [33 * k, Y - 24 * k, 29 * k], r: 12 * k, tilt: norm([-0.25, 1, 0.2]), cym: true, ids: [51, 53, 59], hand: 1 },
    ];
    const kick = { c: [33 * k, Y - 58 * k, 2 * k] as P3, r: 15 * k };
    const flash = (ids: number[]) => {
      let e = 0;
      for (const n of f.notes) {
        if (n[0] > f.t) break;
        if (ids.includes(n[2]) && f.t - n[0] < 0.4) e = Math.max(e, (n[3] / 127) * Math.exp(-(f.t - n[0]) * 9));
      }
      return e;
    };
    // the kick: a drum on its side, both heads, lugs, and a beater that swings in
    const ke = flash([35, 36]);
    for (const dx of [-7 * k, 7 * k]) this.ring(add(kick.c, [dx, 0, 0]), [0, 1, 0], [0, 0, 1], kick.r * (1 + 0.03 * ke), kick.r * (1 + 0.03 * ke), col, dx > 0 ? 1.6 + 1.5 * ke : 1.1, dx > 0 ? 0.7 + 0.3 * ke : 0.6, 36, ke > 0.3);
    for (let i = 0; i < 10; i++) {
      const t = (i / 10) * Math.PI * 2;
      this.P([add(kick.c, [-7 * k, Math.cos(t) * kick.r, Math.sin(t) * kick.r]), add(kick.c, [7 * k, Math.cos(t) * kick.r, Math.sin(t) * kick.r])], col, 0.8, 0.5);
    }
    this.ring(add(kick.c, [7.2 * k, 0, 0]), [0, 1, 0], [0, 0, 1], kick.r * 0.35, kick.r * 0.35, col, 1, 0.6, 20);
    const beat = 1 - Math.min(1, ke * 1.5);
    this.P([add(kick.c, [-9 * k, -kick.r * 0.9, 0]), add(kick.c, [-7.5 * k - beat * 5 * k, -kick.r * 0.1, 0])], col, 1.4, 0.9);
    // drums and cymbals on their stands
    for (const p of pads) {
      const e = flash(p.ids);
      const a = norm(cross(p.tilt, [0, 0, 1]));
      const b = cross(p.tilt, a);
      const wob = p.cym ? 0.12 * e * Math.sin(f.t * 30) : 0;
      const ta = norm(add(a, mul(p.tilt, wob)));
      if (p.cym) {
        for (const rr of [1, 0.66, 0.3]) this.ring(p.c, ta, b, p.r * rr, p.r * rr, col, rr === 1 ? 1.4 + 1.5 * e : 0.9, rr === 1 ? 0.75 + 0.25 * e : 0.5, 32, e > 0.3);
        this.P([add(p.c, mul(p.tilt, 1 * k)), add(p.c, mul(p.tilt, -0.6 * k))], col, 1, 0.8);
      } else {
        const depth = (p.ids[0] === 41 ? 14 : 5.5) * k;
        this.ring(p.c, a, b, p.r, p.r, col, 1.5 + 1.5 * e, 0.8 + 0.2 * e, 30, e > 0.3);
        this.ring(sub(p.c, mul(p.tilt, depth)), a, b, p.r, p.r, col, 1, 0.55, 30);
        for (let i = 0; i < 8; i++) {
          const t = (i / 8) * Math.PI * 2;
          const o = add(mul(a, Math.cos(t) * p.r), mul(b, Math.sin(t) * p.r));
          this.P([add(p.c, o), add(sub(p.c, mul(p.tilt, depth)), o)], col, 0.8, 0.45);
        }
        if (e > 0.05) this.ring(p.c, a, b, p.r * 0.6 * (1 + (1 - e)), p.r * 0.6 * (1 + (1 - e)), V.white, 1.2, e, 24);
      }
      // stand: a pole to the floor with a tripod
      const foot: P3 = [p.c[0], Y - 74 * k, p.c[2]];
      this.P([sub(p.c, mul(p.tilt, p.cym ? 1 : 6 * k)), foot], col, 0.9, 0.5);
      for (let i = 0; i < 3; i++) {
        const t = (i / 3) * Math.PI * 2 + 0.5;
        this.P([add(foot, [0, 8 * k, 0]), add(foot, [Math.cos(t) * 6 * k, 0, Math.sin(t) * 6 * k])], col, 0.8, 0.4);
      }
    }
    // a seat under the drummer
    if (!this.bodyless) this.ring([-3 * k, Y - 64 * k, 0], [1, 0, 0], [0, 0, 1], 9 * k, 9 * k, col, 1, 0.5, 24);
    if (!this.bodyless) this.P([[-3 * k, Y - 64 * k, 0], [-3 * k, Y - 76 * k, 0]], col, 1, 0.5);
    // the sticks: each hand lifts towards its next hit and lands on it
    const handHits = (hand: 1 | -1) =>
      f.notes
        .map((n) => ({ n, pad: pads.find((p) => p.ids.includes(n[2])) }))
        .filter((x) => x.pad && x.pad.hand === hand);
    for (const hand of [1, -1] as const) {
      const hits = handHits(hand);
      let prev: (typeof hits)[number] | undefined, next: (typeof hits)[number] | undefined;
      for (const h of hits) {
        if (h.n[0] <= f.t) prev = h;
        else {
          next = h;
          break;
        }
      }
      const rest = hand === 1 ? pads[1] : pads[0];
      const from = prev?.pad ?? rest, to = next?.pad ?? rest;
      const t0 = prev ? prev.n[0] : f.t - 1, t1 = next ? next.n[0] : f.t + 1;
      const u = clamp((f.t - t0) / Math.max(0.05, t1 - t0), 0, 1);
      const gap = clamp(t1 - t0, 0.08, 0.8);
      const lift = Math.sin(Math.PI * u) * (6 + 12 * gap) * k;
      const tip = add(lerp(from.c, to.c, smooth(u)), [0, lift + 0.5 * k, 0]);
      const handPos = add(tip, [-17 * k, 6 * k + lift * 0.3, -hand * 3 * k]);
      this.P([handPos, tip], V.white, 1.3, 0.95, false, !!prev && f.t - prev.n[0] < 0.06);
      this.ball(tip, 0.8 * k, col, 1, 0.9);
      this.arm(hand, handPos, tip, col);
    }
  }

  dispose(): void {
    this.beams.dispose();
  }
}
