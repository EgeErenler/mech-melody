/**
 * The mech.melody studio: a whole band on one 1080×1920 canvas, drawn frame by frame on demand
 * (no real-time clock), so a headless browser can step it into a Reel. Each singer is a CHIMERA
 * head replaying its recorded telemetry; each player is a head behind a vector instrument that
 * moves with its MIDI notes. English subtitles run underneath.
 *
 * A show is `public/shows/<id>/studio.json` (written by `python3 -m mech build`).
 */
import * as THREE from "three";
import { Anatomy3D } from "../ui/render/anatomy3d.ts";
import { Glow, hfont, label, seg } from "../ui/render/vector.ts";
import { getModel } from "../ui/useModel.ts";
import { VoiceModel } from "../ui/analysis.ts";
import { studioWords as mouthWords } from "./words.ts";
import { TeleTrack } from "../show/tele.ts";
import { sungWords, wordAt, type Melody } from "../show/words.ts";
import { NEUTRAL } from "../wasm/engine.ts";
import { Rig, type RigKind } from "./rig3d.ts";
import type { ViewFlags } from "../ui/render/anatomy.ts";
import type { OverlayFn } from "../ui/components/AnatomyView.tsx";
import type { BodyMix } from "../ui/body.ts";

export const W = 1080;
export const H = 1920;

export interface StudioPart {
  name: string;
  color: string;
  body: "male" | "female" | "child";
  kind: "voice" | "instrument";
  instrument?: string;
  tele?: string;
  melody?: string;
  role?: string;
  /** [start s, end s, midi, velocity] */
  notes?: [number, number, number, number][];
  /** a beatboxer's hits: [time s, beat sound id] */
  hits?: [number, string][];
}

export interface StudioShow {
  title: string;
  artist: string;
  language: string;
  bpm: number;
  downbeat: number;
  duration: number;
  parts: StudioPart[];
  subtitles: { start: number; end: number; text: string; orig: string }[];
}

const VIEW: ViewFlags = { pressure: true, particles: true, grid: false, calipers: false };
const BODIES: Record<string, BodyMix> = { male: { female: 0, child: 0 }, female: { female: 1, child: 0 }, child: { female: 0, child: 1 } };

interface Cell {
  x: number;
  y: number;
  w: number;
  h: number;
  /** the head's share of the cell (the rest is the instrument) */
  headH: number;
  /** where this cell is drawn in the shared WebGL canvas (cells may overlap on screen, not there) */
  gx?: number;
  gy?: number;
}

/** where each part sits: the lead's head big in the middle; the instruments and the other voices
 *  small along the bottom (voices at the outside, instruments between them), heads cut by the frame */
export function layout(parts: StudioPart[]): Cell[] {
  const cells: Cell[] = new Array(parts.length);
  const lead = parts.findIndex((p) => p.kind === "voice" && (p.role ?? "lead") === "lead");
  const voices = parts.map((_, i) => i).filter((i) => i !== lead && parts[i].kind === "voice");
  const band = parts.map((_, i) => i).filter((i) => parts[i].kind === "instrument");
  const others = voices.length + band.length;
  const leadH = others ? 1060 : 1300;
  if (lead >= 0) cells[lead] = { x: 0, y: 175, w: W, h: leadH, headH: leadH };
  // the bottom: voices split to the two sides, the band in the middle
  const half = Math.ceil(voices.length / 2);
  const order = [...voices.slice(0, half), ...band, ...voices.slice(half)];
  const rows = order.length > 6 ? 2 : 1;
  const per = Math.ceil(order.length / rows);
  const top = lead >= 0 ? (rows === 1 ? 1130 : 1105) : 300;
  const rh = rows === 1 ? 360 : 205;
  order.forEach((id, i) => {
    const r = Math.floor(i / per);
    const inRow = Math.min(per, order.length - r * per);
    const cw = Math.round(W / inRow);
    const isInst = parts[id].kind === "instrument";
    const h = isInst ? Math.round(rh * 0.85) : rh;
    // the second row sits half a pane over, between the heads of the first
    const shift = r % 2 && inRow < per + 1 ? 0 : 0;
    cells[id] = { x: (i % per) * cw + shift, y: top + r * (rh - 15) + (isInst || rows > 1 ? 0 : 30), w: cw, h, headH: h };
  });
  // pack the cells into the WebGL canvas, shelf by shelf
  let gx = 0, gy = 0, shelf = 0;
  for (const c of cells) {
    if (!c) continue;
    if (gx + c.w > 2200) {
      gx = 0;
      gy += shelf;
      shelf = 0;
    }
    c.gx = gx;
    c.gy = gy;
    gx += c.w;
    shelf = Math.max(shelf, c.headH);
  }
  return cells;
}

/** the WebGL canvas holding every cell */
export function atlasSize(cells: Cell[]): [number, number] {
  let w = 1, h = 1;
  for (const c of cells) if (c) {
    w = Math.max(w, (c.gx ?? 0) + c.w);
    h = Math.max(h, (c.gy ?? 0) + c.headH);
  }
  return [w, h];
}

const BEAT_WORD: Record<string, string> = { B: "BOOM", Pf: "PF", K: "KA", "^K": "K!", t: "TS", tss: "TSSS", kch: "KSHH", m: "MM", "!": "TOK", "P!": "POP" };

const isLead = (p: StudioPart) => p.kind === "voice" && (p.role ?? "lead") === "lead";

function rigKind(p: StudioPart): RigKind {
  if (p.kind === "instrument") {
    const look: Record<string, RigKind> = { bells: "piano", celesta: "piano", organ: "synth", pad: "synth", "electric-guitar": "guitar", harp: "strings", brass: "synth", flute: "synth", sax: "synth" };
    return look[p.instrument ?? ""] ?? ((p.instrument ?? "synth") as RigKind);
  }
  const role = p.role ?? "lead";
  if (role === "lead") return "lead";
  if (role === "beatbox") return "beatbox";
  if (role === "choir" || role === "bass") return "choir";
  return "singer";
}

/** a hex colour as rgba */
function rgba(hex: string, a: number): string {
  const v = parseInt(hex.slice(1), 16);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${a})`;
}

class Member {
  readonly r = new Anatomy3D();
  readonly model: VoiceModel;
  readonly canvas = document.createElement("canvas");
  readonly ctx: CanvasRenderingContext2D;
  private overlay: OverlayFn | undefined;
  private notes: [number, number, number, number][];
  private readonly rig: Rig;
  private still: { target: THREE.Vector3; dir: THREE.Vector3; dist: number } | null = null;

  constructor(
    readonly part: StudioPart,
    readonly cell: Cell,
    readonly track: TeleTrack | null,
    melody: Melody | null,
    readonly facing: 1 | -1,
    private readonly show: StudioShow,
    private readonly now: () => number,
  ) {
    const body = track?.body ?? BODIES[part.body] ?? BODIES.male;
    this.model = getModel(body, track?.sampleRate ?? 48000);
    this.r.centred = true;
    this.r.facing = facing;
    this.notes = part.notes ?? [];
    this.rig = new Rig(rigKind(part), "#3487c4");
    this.r.scene.add(this.rig.beams.group);
    this.canvas.width = this.cell.w;
    this.canvas.height = this.cell.h;
    this.ctx = this.canvas.getContext("2d")!;
    if (part.hits?.length) {
      const hits = part.hits;
      this.overlay = mouthWords(() => {
        const t = this.now();
        let k = -1;
        for (let i = 0; i < hits.length && hits[i][0] <= t; i++) k = i;
        if (k < 0 || t - hits[k][0] > 0.22) return null;
        return { key: k, text: BEAT_WORD[hits[k][1]] ?? hits[k][1].toUpperCase() };
      }, part.color, facing);
    } else if (melody && melody.syllables) {
      const words = sungWords(melody, melody.syllables);
      this.overlay = mouthWords(() => wordAt(words, this.now()), part.color, facing);
    }
  }

  /** the beat as a 0..1 phase, for nods and sways */
  private beat(t: number): number {
    const b = ((t - this.show.downbeat) * this.show.bpm) / 60;
    return b - Math.floor(b);
  }

  private frameState: { f: ReturnType<VoiceModel["frame"]> | null; a: ReturnType<typeof VoiceModel.articFromArray>; singing: boolean } | null = null;

  /** draw the head into its own viewport of the shared WebGL canvas (one readback for the band) */
  renderGL(gl: THREE.WebGLRenderer, glH: number, t: number, dt: number): void {
    const { w } = this.cell;
    const gx = this.cell.gx ?? 0, gy = this.cell.gy ?? 0;
    const hh = this.cell.headH;
    const r = this.r;
    r.resize(w, hh);
    this.rig.beams.setResolution(w, hh);
    const ph = this.beat(t);
    const sway = 0.06 * Math.sin(2 * Math.PI * (ph / 2 + (t * this.show.bpm) / 120)) + 0.04 * Math.sin(t * 0.37 + this.cell.x);
    const bounds = r.bounds(this.model);
    let tele = null, singing = false, f = null as ReturnType<VoiceModel["frame"]> | null, a = NEUTRAL;
    if (this.part.kind === "instrument") {
      // the instrument alone: drawn first, then the camera fitted to what was drawn
      this.rig.fit(bounds.y0, bounds.k);
      this.rig.update({ t, dt, level: 0, notes: this.notes }, bounds.center);
      // the instrument stands still: the camera is fitted once, to the instrument at rest, and never
      // sways (only its playing parts move: strings, bow, sticks, beater, keys)
      if (!this.still) {
        // fitted to the instrument at rest (no note sounding), the same in every capture worker
        this.rig.update({ t: -100, dt: 0, level: 0, notes: this.notes }, bounds.center);
        const fr0 = this.rig.frameOnDrawing(w / hh, this.facing);
        this.still = { ...fr0, dist: fr0.dist * 1.08 };
        this.rig.update({ t, dt, level: 0, notes: this.notes }, bounds.center);
      }
      const fr = this.still;
      const off = fr.dir.clone().multiplyScalar(fr.dist);
      r.camera.position.copy(fr.target).add(off);
      r.camera.lookAt(fr.target);
      const fog = r.scene.fog as THREE.Fog;
      fog.near = fr.dist - 200;
      fog.far = fr.dist + 400;
    } else {
      tele = this.track ? this.track.message(this.track.indexAt(t)) : null;
      singing = !!tele && tele.t.outRms > 1e-4;
      a = tele ? VoiceModel.articFromArray(tele.t.artic) : NEUTRAL;
      r.face.update(singing ? tele : null, dt);
      const lead = (this.part.role ?? "lead") === "lead";
      // the head alone, filling its pane: the lead whole and big; the others from the chin up, cut by the frame
      const p = r.poses(this.model);
      const lo = lead ? bounds.y0 + 1 * bounds.k : bounds.y0 + 9 * bounds.k;
      const hi = bounds.y1 + (lead ? 1.5 : 0.5) * bounds.k;
      const half = Math.tan((r.camera.fov * Math.PI) / 360);
      const dist = (lead ? 1.04 : 0.86) * Math.max((hi - lo) / 2 / half, (hi - lo) * 0.62 / (half * (w / hh)));
      const target = new THREE.Vector3(p.target.x + (lead ? 1.5 : 0) * bounds.k, (lo + hi) / 2 - (lead ? 0 : 3 * bounds.k), 0);
      const off = p.home.clone().sub(p.target).normalize().multiplyScalar(dist).applyAxisAngle(new THREE.Vector3(0, 1, 0), sway);
      r.camera.position.copy(target).add(off);
      r.camera.lookAt(target);
      // the lead's head in the middle of the screen (its words take the space to its right)
      if (lead) {
        r.camera.setViewOffset(w, hh, -Math.round(w * 0.07 * this.facing), 0, w, hh);
        r.camera.updateProjectionMatrix();
      }
      f = this.model.frame(a);
      r.update(this.model, f, a, singing ? tele : null, VIEW, dt);
    }
    const by = glH - gy - hh; // WebGL counts rows from the bottom
    gl.setViewport(gx, by, w, hh);
    gl.setScissor(gx, by, w, hh);
    gl.render(r.scene, r.camera);
    this.frameState = { f, a, singing };
  }

  /** the 2D layer: the head's pixels in the part's colour, the words, the instrument, the frame */
  draw(glFrame: HTMLCanvasElement, t: number, dt: number): void {
    const { w } = this.cell;
    const hh = this.cell.headH;
    const r = this.r;
    const { f, a, singing } = this.frameState!;
    const c = this.ctx;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = "source-over";
    c.globalAlpha = 1;
    c.fillStyle = "#000";
    c.fillRect(0, 0, w, this.cell.h);
    c.drawImage(glFrame, this.cell.gx ?? 0, this.cell.gy ?? 0, w, hh, 0, 0, w, hh);
    // the member's colour: keep each line's brightness, take the hue of the part
    c.globalCompositeOperation = "color";
    c.fillStyle = this.part.color;
    c.fillRect(0, 0, w, hh);
    c.globalCompositeOperation = "source-over";
    if (f) this.overlay?.(c, r.faceView(this.model, f, a), this.model, f, a, { w, h: hh, dt });
    // soft edges, so overlapping panes melt into the black (no boxes)
    c.globalCompositeOperation = "destination-in";
    const gV = c.createLinearGradient(0, 0, 0, this.cell.h);
    const lead = this.part.kind === "voice" && (this.part.role ?? "lead") === "lead";
    gV.addColorStop(0, "rgba(0,0,0,1)");
    gV.addColorStop(lead ? 0.9 : 0.75, "rgba(0,0,0,1)");
    gV.addColorStop(1, lead ? "rgba(0,0,0,0.0)" : "rgba(0,0,0,0.15)");
    c.fillStyle = gV;
    c.fillRect(0, 0, w, this.cell.h);
    c.globalCompositeOperation = "source-over";
    void singing;
    void t;
  }

  private active(t: number): [number, number, number, number][] {
    const out: [number, number, number, number][] = [];
    for (const n of this.notes) {
      if (n[0] > t) break;
      if (t < n[1] + 0.05) out.push(n);
    }
    return out;
  }


}

export class Studio {
  readonly canvas: HTMLCanvasElement;
  private readonly ctx: CanvasRenderingContext2D;
  private readonly gl: THREE.WebGLRenderer;
  private readonly glCanvas = document.createElement("canvas");
  /** the WebGL frame copied out once per frame (each read of a WebGL canvas is a slow sync) */
  private readonly glFrame = document.createElement("canvas");
  private readonly glow = new Glow(2.2, 0.85, 4);
  private members: Member[] = [];
  private t = 0;

  constructor(
    readonly show: StudioShow,
    canvas: HTMLCanvasElement,
  ) {
    this.canvas = canvas;
    canvas.width = W;
    canvas.height = H;
    this.ctx = canvas.getContext("2d")!;
    this.gl = new THREE.WebGLRenderer({ canvas: this.glCanvas, antialias: true, alpha: false, preserveDrawingBuffer: true });
    this.gl.outputColorSpace = THREE.LinearSRGBColorSpace;
    this.gl.setClearColor(0x000000, 1);
    this.gl.setPixelRatio(1);
    this.gl.setScissorTest(true);
  }

  private glH = 1;

  /** size the shared WebGL canvas to hold every cell side by side */
  private atlas(cells: Cell[]): void {
    const [aw, ah] = atlasSize(cells);
    this.gl.setSize(aw, ah, false);
    this.glFrame.width = aw;
    this.glFrame.height = ah;
    this.glH = ah;
  }

  static async load(base: string, canvas: HTMLCanvasElement): Promise<Studio> {
    const get = async (u: string) => {
      const r = await fetch(base + u);
      if (!r.ok) throw new Error(`${base + u}: ${r.status}`);
      return r;
    };
    const show = (await (await get("studio.json")).json()) as StudioShow;
    const s = new Studio(show, canvas);
    const cells = layout(show.parts);
    s.atlas(cells);
    for (let i = 0; i < show.parts.length; i++) {
      const p = show.parts[i];
      const track = p.tele ? new TeleTrack(await (await get(p.tele)).arrayBuffer()) : null;
      const melody = p.melody ? ((await (await get(p.melody)).json()) as Melody) : null;
      const cell = cells[i];
      // heads in the left column look right, the right column looks left: towards each other
      // the lead looks right (its words come out to the right); the others look towards the middle
      const facing: 1 | -1 = cell.w < W * 0.7 && cell.x + cell.w / 2 > W / 2 + 10 ? -1 : 1;
      s.members.push(new Member(p, cell, track, melody, facing, show, () => s.t));
    }
    await document.fonts.load(hfont(40, "m"));
    await document.fonts.load(hfont(40, "l"));
    await document.fonts.load(`600 40px Archivo`);
    return s;
  }

  /** draw the song at time t (s); frames must be drawn in order for the faces' motion */
  frame(t: number, dt: number, opts: { cover?: boolean } = {}): void {
    this.t = t;
    const c = this.ctx;
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalCompositeOperation = "source-over";
    c.globalAlpha = 1;
    c.fillStyle = "#000";
    c.fillRect(0, 0, W, H);
    for (const m of this.members) m.renderGL(this.gl, this.glH, t, dt);
    const g = this.glFrame.getContext("2d")!;
    g.drawImage(this.glCanvas, 0, 0);
    // the lead last: it sits in front of everything
    const order = [...this.members].sort((x, y) => (isLead(x.part) ? 1 : 0) - (isLead(y.part) ? 1 : 0));
    for (const m of order) {
      m.draw(this.glFrame, t, dt);
      c.globalCompositeOperation = "lighter";
      c.drawImage(m.canvas, m.cell.x, m.cell.y);
      c.globalCompositeOperation = "source-over";
    }
    this.header(opts.cover);
    if (opts.cover) this.coverText();
    else this.subtitle(t);
    this.footer(t, opts.cover);
    this.glow.apply(this.canvas, c, 1);
  }

  private header(cover?: boolean): void {
    const c = this.ctx;
    const lead = this.show.parts[0]?.color ?? "#ffc45a";
    label(c, "MECH.MELODY", 40, 64, { size: 30, color: lead, weight: "m", ls: 0.3 });
    if (!cover) {
      label(c, this.show.title.toUpperCase(), 40, 128, { size: 50, color: "#eef6f8", weight: "m", ls: 0.06 });
      label(c, `${this.show.artist.toUpperCase()}  ·  ${this.show.parts.length} ROBOTS`, 40, 172, { size: 22, color: "#eef6f8", alpha: 0.55, ls: 0.16 });
    }
    seg(c, 40, 186, W - 40, 186, lead, 1, { alpha: 0.35 });
  }

  private subtitle(t: number): void {
    const s = this.show.subtitles.find((x) => t >= x.start && t < x.end);
    if (!s) return;
    const c = this.ctx;
    const u = Math.min(1, (t - s.start) / 0.18, (s.end - t) / 0.18);
    const y = 1655;
    c.save();
    c.globalAlpha = Math.max(0, u);
    c.font = `600 58px Archivo`;
    c.textAlign = "center";
    c.textBaseline = "middle";
    const lines = wrap(c, s.text, W - 120);
    const lh = 70;
    const y0 = y - ((lines.length - 1) * lh) / 2;
    const boxH = lines.length * lh + 40;
    c.fillStyle = "rgba(0,0,0,0.72)";
    c.fillRect(40, y0 - lh / 2 - 20, W - 80, boxH);
    c.fillStyle = "#ffffff";
    lines.forEach((ln, i) => c.fillText(ln, W / 2, y0 + i * lh));
    const orig = (s.orig ?? "").replace(/[|_]/g, "").replace(/\s+/g, " ").trim();
    if (this.show.language !== "en" && orig && orig !== s.text) {
      c.font = `italic 400 32px Archivo`;
      c.fillStyle = rgba(this.show.parts[0]?.color ?? "#ffc45a", 0.85);
      const o = wrap(c, orig, W - 140);
      o.slice(0, 2).forEach((ln, i) => c.fillText(ln, W / 2, y0 - lh / 2 - 50 - (o.slice(0, 2).length - 1 - i) * 40));
    }
    c.restore();
  }

  private coverText(): void {
    const c = this.ctx;
    c.save();
    c.fillStyle = "rgba(0,0,0,0.78)";
    c.fillRect(0, 1380, W, 420);
    c.textAlign = "center";
    c.textBaseline = "middle";
    c.fillStyle = "#ffffff";
    c.font = `800 ${fitSize(c, this.show.title.toUpperCase(), W - 100, 120, "800", "Archivo")}px Archivo`;
    c.fillText(this.show.title.toUpperCase(), W / 2, 1520);
    c.font = `500 46px Archivo`;
    c.fillStyle = this.show.parts[0]?.color ?? "#ffc45a";
    c.fillText(`${this.show.artist} · sung by ${this.show.parts.filter((p) => p.kind === "voice").length} robot voices`, W / 2, 1630);
    c.restore();
  }

  private footer(t: number, cover?: boolean): void {
    const c = this.ctx;
    const col = this.show.parts[0]?.color ?? "#ffc45a";
    if (!cover) {
      const u = Math.min(1, t / this.show.duration);
      seg(c, 40, 1850, W - 40, 1850, col, 1, { alpha: 0.25 });
      seg(c, 40, 1850, 40 + (W - 80) * u, 1850, col, 3, { hot: true });
    }
    label(c, "@MECH.MELODY", W / 2, 1890, { size: 22, color: "#eef6f8", alpha: 0.6, align: "center", ls: 0.25 });
  }
}

function wrap(c: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const words = text.split(/\s+/);
  const lines: string[] = [];
  let cur = "";
  for (const w of words) {
    const next = cur ? `${cur} ${w}` : w;
    if (c.measureText(next).width > maxW && cur) {
      lines.push(cur);
      cur = w;
    } else cur = next;
  }
  if (cur) lines.push(cur);
  return lines;
}

function fitSize(c: CanvasRenderingContext2D, text: string, maxW: number, size: number, weight: string, family: string): number {
  let s = size;
  for (; s > 30; s -= 4) {
    c.font = `${weight} ${s}px ${family}`;
    if (c.measureText(text).width <= maxW) break;
  }
  return s;
}
