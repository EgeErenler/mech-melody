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
import { Glow, hfont, label, seg, beamPts } from "../ui/render/vector.ts";
import { getModel } from "../ui/useModel.ts";
import { VoiceModel } from "../ui/analysis.ts";
import { mouthWords } from "../ui/mouthWords.ts";
import { TeleTrack } from "../show/tele.ts";
import { sungWords, wordAt, type Melody } from "../show/words.ts";
import { NEUTRAL } from "../wasm/engine.ts";
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
  instrument?: "guitar" | "bass" | "piano" | "synth" | "strings" | "drums";
  tele?: string;
  melody?: string;
  role?: string;
  /** [start s, end s, midi, velocity] */
  notes?: [number, number, number, number][];
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
}

/** where each part sits: singers first, the lead biggest */
export function layout(n: number, top = 200, bottom = 1470): Cell[] {
  const area = bottom - top;
  const cells: Cell[] = [];
  const pad = 14;
  const grid = (rows: number[][], y0: number, hh: number) => {
    rows.forEach((row, r) => {
      const cw = W / row.length;
      row.forEach((_, c) => cells.push({ x: c * cw + pad / 2, y: y0 + r * hh + pad / 2, w: cw - pad, h: hh - pad, headH: hh - pad }));
    });
  };
  if (n <= 1) grid([[0]], top, area);
  else if (n === 2) grid([[0], [1]], top, area / 2);
  else if (n === 3) {
    grid([[0]], top, area * 0.5);
    grid([[1, 2]], top + area * 0.5, area * 0.5);
  } else {
    const rows: number[][] = [];
    let k = 0;
    if (n % 2 === 1) rows.push([k++]);
    while (k < n) rows.push([k++, k++]);
    grid(rows, top, area / rows.length);
  }
  return cells;
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
    if (part.kind === "instrument") this.cell = { ...cell, headH: Math.round(cell.h * (cell.w > 700 ? 0.62 : 0.56)) };
    this.canvas.width = this.cell.w;
    this.canvas.height = this.cell.h;
    this.ctx = this.canvas.getContext("2d")!;
    if (melody && melody.syllables) {
      const words = sungWords(melody, melody.syllables);
      this.overlay = mouthWords(() => wordAt(words, this.now()), part.color, facing);
    }
  }

  /** the beat as a 0..1 phase, for nods and sways */
  private beat(t: number): number {
    const b = ((t - this.show.downbeat) * this.show.bpm) / 60;
    return b - Math.floor(b);
  }

  private frameState: { f: ReturnType<VoiceModel["frame"]>; a: ReturnType<typeof VoiceModel.articFromArray>; singing: boolean } | null = null;

  /** draw the head into its own viewport of the shared WebGL canvas (one readback for the band) */
  renderGL(gl: THREE.WebGLRenderer, t: number, dt: number): void {
    const { x, y, w } = this.cell;
    const hh = this.cell.headH;
    const r = this.r;
    r.resize(w, hh);
    const tele = this.track ? this.track.message(this.track.indexAt(t)) : null;
    const singing = !!tele && tele.t.outRms > 1e-4;
    const a = tele ? VoiceModel.articFromArray(tele.t.artic) : NEUTRAL;
    r.face.update(singing ? tele : null, dt);
    // the camera: the turned home view, swaying a little with the beat
    const p = r.poses(this.model);
    const ph = this.beat(t);
    const off = p.home.clone().sub(p.target);
    const sway = (this.part.kind === "instrument" ? 0.09 : 0.05) * Math.sin(2 * Math.PI * (ph / 2 + (t * this.show.bpm) / 120));
    off.applyAxisAngle(new THREE.Vector3(0, 1, 0), sway + 0.03 * Math.sin(t * 0.37 + this.cell.x));
    r.camera.position.copy(p.target).add(off);
    r.camera.lookAt(p.target);
    const f = this.model.frame(a);
    r.update(this.model, f, a, singing ? tele : null, VIEW, dt);
    const by = H - y - hh; // WebGL counts rows from the bottom
    gl.setViewport(x, by, w, hh);
    gl.setScissor(x, by, w, hh);
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
    c.drawImage(glFrame, this.cell.x, this.cell.y, w, hh, 0, 0, w, hh);
    // the band member's colour: keep each line's brightness, take the hue of the part
    c.globalCompositeOperation = "color";
    c.fillStyle = this.part.color;
    c.fillRect(0, 0, w, hh);
    c.globalCompositeOperation = "source-over";
    this.overlay?.(c, r.faceView(this.model, f, a), this.model, f, a, { w, h: hh, dt });
    if (this.part.kind === "instrument") this.drawInstrument(t);
    // name plate
    const sub = this.part.kind === "instrument" ? (this.part.instrument ?? "").toUpperCase() : `${this.part.body.toUpperCase()} VOICE`;
    label(c, this.part.name.toUpperCase(), 18, 30, { size: 22, color: this.part.color, weight: "m", ls: 0.12 });
    if (sub !== this.part.name.toUpperCase()) label(c, sub, 18, 56, { size: 15, color: this.part.color, alpha: 0.6, ls: 0.16 });
    c.save();
    c.strokeStyle = rgba(this.part.color, singing || this.active(t).length ? 0.55 : 0.22);
    c.lineWidth = 1.5;
    c.strokeRect(1, 1, w - 2, this.cell.h - 2);
    c.restore();
  }

  private active(t: number): [number, number, number, number][] {
    const out: [number, number, number, number][] = [];
    for (const n of this.notes) {
      if (n[0] > t) break;
      if (t < n[1] + 0.05) out.push(n);
    }
    return out;
  }

  /** notes struck in the last `win` seconds, with how long ago */
  private struck(t: number, win: number): { n: [number, number, number, number]; age: number }[] {
    const out = [];
    for (const n of this.notes) {
      if (n[0] > t) break;
      if (t - n[0] < win) out.push({ n, age: t - n[0] });
    }
    return out;
  }

  private drawInstrument(t: number): void {
    const c = this.ctx;
    const col = this.part.color;
    const x0 = 0, y0 = this.cell.headH - 20, w = this.cell.w, h = this.cell.h - this.cell.headH + 10;
    const cx = x0 + w / 2, cy = y0 + h / 2;
    const k = Math.min(w / 500, h / 260);
    const kind = this.part.instrument;
    if (kind === "guitar" || kind === "bass") {
      const nStr = kind === "bass" ? 4 : 6;
      const bodyR = 62 * k;
      const len = Math.min((kind === "bass" ? 440 : 400) * k, w * 0.62);
      const ang = -0.28;
      // the neck points the way the head looks; the whole guitar centred in the cell
      const ux = Math.cos(ang) * this.facing, uy = Math.sin(ang);
      const bx = cx - ux * (len - bodyR) * 0.5, by = cy + 18 * k - uy * (len - bodyR) * 0.5;
      const P = (s: number, q: number): [number, number] => [bx + ux * s - uy * q, by + uy * s + ux * q];
      // body: two lobes and a waist
      const outline: [number, number][] = [];
      for (let i = 0; i <= 64; i++) {
        const a = (i / 64) * Math.PI * 2;
        const s = Math.cos(a) * bodyR * 1.25;
        const lobe = s < 0 ? 1 : 0.8;
        const waist = 1 - 0.18 * Math.exp(-((s / bodyR - 0.1) ** 2) * 8);
        outline.push(P(s, Math.sin(a) * bodyR * lobe * waist));
      }
      beamPts(c, outline, col, 2, { close: true });
      const hole = [];
      for (let i = 0; i <= 32; i++) hole.push(P(Math.cos((i / 32) * 6.283) * 18 * k + 12 * k, Math.sin((i / 32) * 6.283) * 18 * k));
      beamPts(c, hole as [number, number][], col, 1.2, { alpha: 0.7 });
      // neck and headstock
      const nw = (kind === "bass" ? 13 : 11) * k;
      beamPts(c, [P(bodyR * 0.9, -nw), P(len, -nw), P(len + 40 * k, -nw * 1.4), P(len + 40 * k, nw * 1.4), P(len, nw), P(bodyR * 0.9, nw)], col, 1.6);
      for (let f = 1; f < 12; f++) {
        const s = bodyR * 0.9 + (len - bodyR * 0.9) * (1 - 1 / 2 ** (f / 6)) * 1.9;
        if (s < len) seg(c, ...P(s, -nw), ...P(s, nw), col, 0.8, { alpha: 0.4 });
      }
      // strings: each vibrates when a note on it sounds
      const act = this.active(t);
      for (let i = 0; i < nStr; i++) {
        const q = -nw * 0.8 + (i * 1.6 * nw) / (nStr - 1);
        const hit = act.find((n) => Math.floor((n[2] % 24) / (24 / nStr)) === i) ?? (act.length > i ? act[i] : undefined);
        const amp = hit ? 4.5 * k * Math.exp(-(t - hit[0]) * 3) : 0;
        const pts: [number, number][] = [];
        for (let s = 0; s <= 24; s++) {
          const u = s / 24;
          const wob = amp * Math.sin(Math.PI * u) * Math.sin(t * 90 + i * 1.7);
          pts.push(P(-bodyR * 0.6 + (len + bodyR * 0.6) * u, q + wob));
        }
        beamPts(c, pts, col, hit ? 1.6 : 0.9, { alpha: hit ? 1 : 0.55, hot: !!hit });
      }
    } else if (kind === "piano" || kind === "synth") {
      const keys = 21, kw = Math.min(40 * k, (w - 40) / keys), kh = 110 * k;
      const left = cx - (keys * kw) / 2, top = cy - kh / 2;
      const act = this.active(t);
      const lit = new Set(act.map((n) => n[2]));
      const white = [0, 2, 4, 5, 7, 9, 11];
      const base = 48;
      let wi = 0;
      for (let m = base; wi < keys; m++) {
        if (!white.includes(m % 12)) continue;
        const on = [...lit].some((p) => p % 36 === m % 36 || p === m);
        c.save();
        c.fillStyle = rgba(col, on ? 0.55 : 0);
        c.fillRect(left + wi * kw + 1, top + 1, kw - 2, kh - 2);
        c.restore();
        beamPts(c, [[left + wi * kw, top], [left + (wi + 1) * kw, top], [left + (wi + 1) * kw, top + kh], [left + wi * kw, top + kh]], col, 1.2, { close: true, alpha: on ? 1 : 0.6 });
        if (white.includes((m + 1) % 12) === false && wi < keys - 1) {
          const on2 = [...lit].some((p) => p % 36 === (m + 1) % 36);
          c.save();
          c.fillStyle = on2 ? rgba(col, 0.8) : "#000";
          c.fillRect(left + (wi + 0.68) * kw, top, kw * 0.64, kh * 0.6);
          c.restore();
          beamPts(c, [[left + (wi + 0.68) * kw, top], [left + (wi + 1.32) * kw, top], [left + (wi + 1.32) * kw, top + kh * 0.6], [left + (wi + 0.68) * kw, top + kh * 0.6]], col, 1, { close: true, alpha: 0.8 });
        }
        wi++;
      }
      if (kind === "synth") label(c, "SYNTH", cx, top - 12 * k, { size: 14, color: col, align: "center", alpha: 0.7 });
    } else if (kind === "strings") {
      // a violin and its bow
      const sx = cx - this.facing * 40 * k, sy = cy;
      const outline: [number, number][] = [];
      for (let i = 0; i <= 64; i++) {
        const a = (i / 64) * Math.PI * 2;
        const yy = Math.sin(a) * 70 * k;
        const xx = Math.cos(a) * 34 * k * (1 - 0.25 * Math.exp(-((yy / (70 * k)) ** 2) * 10));
        outline.push([sx + xx, sy + yy]);
      }
      beamPts(c, outline, col, 1.8, { close: true });
      const act = this.active(t);
      for (let i = 0; i < 4; i++) seg(c, sx - 9 * k + i * 6 * k, sy - 120 * k, sx - 9 * k + i * 6 * k, sy + 50 * k, col, act.length ? 1.2 : 0.8, { alpha: 0.7 });
      const bowPos = act.length ? Math.sin(t * 2.4) * 60 * k : 0;
      seg(c, sx - 130 * k + bowPos, sy + 18 * k, sx + 130 * k + bowPos, sy - 2 * k, col, 1.6, { hot: act.length > 0 });
    } else {
      // drums: kick, snare, hats, a tom and a crash; each flashes when it is hit
      const hits = this.struck(t, 0.35);
      const flash = (notes: number[]) => {
        let e = 0;
        for (const { n, age } of hits) if (notes.includes(n[2])) e = Math.max(e, (n[3] / 127) * Math.exp(-age * 9));
        return e;
      };
      const ring = (x: number, y: number, rx: number, ry: number, e: number, fill = false) => {
        const pts: [number, number][] = [];
        for (let i = 0; i <= 40; i++) pts.push([x + Math.cos((i / 40) * 6.283) * rx * (1 + 0.05 * e), y + Math.sin((i / 40) * 6.283) * ry * (1 + 0.05 * e)]);
        if (fill && e > 0.02) {
          c.save();
          c.fillStyle = rgba(col, 0.45 * e);
          c.beginPath();
          pts.forEach(([px, py], i) => (i ? c.lineTo(px, py) : c.moveTo(px, py)));
          c.fill();
          c.restore();
        }
        beamPts(c, pts, col, 1.4 + 1.6 * e, { close: true, alpha: 0.55 + 0.45 * e, hot: e > 0.3 });
      };
      const kick = flash([35, 36]);
      ring(cx, cy + 25 * k, 70 * k, 70 * k, kick, true);
      ring(cx, cy + 25 * k, 22 * k, 22 * k, kick);
      const snare = flash([37, 38, 39, 40]);
      ring(cx - 110 * k, cy - 10 * k, 46 * k, 14 * k, snare, true);
      seg(c, cx - 110 * k, cy + 4 * k, cx - 110 * k, cy + 80 * k, col, 1, { alpha: 0.5 });
      const tom = flash([41, 43, 45, 47, 48, 50]);
      ring(cx + 30 * k, cy - 70 * k, 32 * k, 11 * k, tom, true);
      const hat = flash([42, 44, 46]);
      const hy = cy - 55 * k - hat * 5 * k;
      ring(cx - 170 * k, hy, 44 * k, 7 * k, hat, true);
      ring(cx - 170 * k, cy - 48 * k, 44 * k, 7 * k, hat * 0.6);
      seg(c, cx - 170 * k, cy - 40 * k, cx - 170 * k, cy + 90 * k, col, 1, { alpha: 0.5 });
      const crash = flash([49, 51, 52, 55, 57, 59]);
      const tilt = crash * 0.25;
      const cxr = cx + 150 * k, cyr = cy - 80 * k;
      beamPts(c, [[cxr - 55 * k, cyr + 12 * k * (1 + tilt * 3)], [cxr + 55 * k, cyr - 12 * k * (1 + tilt * 3)]], col, 2 + 2 * crash, { hot: crash > 0.3, alpha: 0.6 + 0.4 * crash });
      seg(c, cxr, cyr, cxr, cy + 90 * k, col, 1, { alpha: 0.5 });
    }
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
    this.gl.setSize(W, H, false);
    this.gl.setScissorTest(true);
    this.glFrame.width = W;
    this.glFrame.height = H;
  }

  static async load(base: string, canvas: HTMLCanvasElement): Promise<Studio> {
    const get = async (u: string) => {
      const r = await fetch(base + u);
      if (!r.ok) throw new Error(`${base + u}: ${r.status}`);
      return r;
    };
    const show = (await (await get("studio.json")).json()) as StudioShow;
    const s = new Studio(show, canvas);
    const cells = layout(show.parts.length);
    for (let i = 0; i < show.parts.length; i++) {
      const p = show.parts[i];
      const track = p.tele ? new TeleTrack(await (await get(p.tele)).arrayBuffer()) : null;
      const melody = p.melody ? ((await (await get(p.melody)).json()) as Melody) : null;
      const cell = cells[i];
      // heads in the left column look right, the right column looks left: towards each other
      const facing: 1 | -1 = cell.x > W / 3 && cell.w < W * 0.7 ? -1 : 1;
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
    for (const m of this.members) m.renderGL(this.gl, t, dt);
    const g = this.glFrame.getContext("2d")!;
    g.drawImage(this.glCanvas, 0, 0);
    for (const m of this.members) {
      m.draw(this.glFrame, t, dt);
      c.drawImage(m.canvas, m.cell.x, m.cell.y);
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
    const y = 1590;
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
