/**
 * studio.html?id=<show>            plays the show in the browser (with its mix)
 * studio.html?id=<show>&capture=1  waits to be driven: window.studio.frame(i) draws frame i
 *                                  (tools: mech/render_video.ts steps it into a video)
 */
import "../ui/styles.css";
import { initChimera } from "../wasm/chimera.ts";

declare global {
  interface Window {
    studio?: { duration: number; fps: number; frame(i: number): void; cover(t: number): void; jpeg(q?: number): string };
    studioError?: string;
  }
}

const q = new URLSearchParams(location.search);
const id = q.get("id") ?? "";
const capture = q.has("capture");
const fps = Number(q.get("fps") ?? 30);
const msg = document.getElementById("msg")!;

async function boot(): Promise<void> {
  await initChimera();
  const { Studio } = await import("./studio.ts");
  const canvas = document.getElementById("stage") as HTMLCanvasElement;
  const base = `${import.meta.env.BASE_URL}shows/${id}/`;
  const s = await Studio.load(base, canvas);
  if (capture) {
    document.body.classList.add("capture");
    let last = -1;
    window.studio = {
      duration: s.show.duration,
      fps,
      frame(i: number) {
        // frames in order: a jump forward runs the frames in between unseen (the faces need them)
        for (let k = last + 1; k < i; k++) s.frame(k / fps, 1 / fps);
        s.frame(i / fps, 1 / fps);
        last = i;
      },
      cover(t: number) {
        const start = Math.max(0, t - 1.5);
        for (let x = start; x < t; x += 1 / fps) s.frame(x, 1 / fps);
        s.frame(t, 1 / fps, { cover: true });
      },
      jpeg: (quality = 0.92) => canvas.toDataURL("image/jpeg", quality),
    };
    return;
  }
  const audio = new Audio(base + "mix.wav");
  const btn = document.getElementById("play") as HTMLButtonElement;
  btn.onclick = () => (audio.paused ? audio.play() : audio.pause());
  audio.onplay = () => (btn.textContent = "Pause");
  audio.onpause = () => (btn.textContent = "Play");
  let prev = performance.now();
  const loop = (now: number) => {
    const dt = Math.min(0.1, (now - prev) / 1000);
    prev = now;
    s.frame(audio.currentTime, dt);
    requestAnimationFrame(loop);
  };
  requestAnimationFrame(loop);
}

boot().catch((e: unknown) => {
  const text = e instanceof Error ? e.message : String(e);
  window.studioError = text;
  msg.textContent = `The studio could not load: ${text}`;
});
