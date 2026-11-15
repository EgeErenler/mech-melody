/**
 * A studio show -> an Instagram Reel (1080×1920, 30 fps, H.264 + AAC) and its cover image.
 *
 *   bun mech/render_video.ts <show-id> [--fps 30] [--seconds N] [--cover-at S]
 *
 * Starts Vite, opens studio.html?capture=1 in headless Chrome, draws each frame, pipes the JPEGs
 * to ffmpeg with the show's mix. Writes out/<id>/reel.mp4 and out/<id>/cover.jpg.
 */
import { chromium } from "playwright-core";
import { spawn, spawnSync, type ChildProcess } from "node:child_process";
import { mkdirSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { resolve } from "node:path";
import { cpus } from "node:os";

const root = resolve(import.meta.dir, "..");
const args = process.argv.slice(2);
const id = args[0];
if (!id) throw new Error("usage: bun mech/render_video.ts <show-id> [--fps 30] [--seconds N] [--cover-at S]");
const opt = (name: string, d: number) => {
  const i = args.indexOf(name);
  return i >= 0 ? Number(args[i + 1]) : d;
};
const fps = opt("--fps", 30);
const port = opt("--port", 5390);
const outDir = resolve(root, "out", id);
mkdirSync(outDir, { recursive: true });

function chromePath(): string {
  for (const p of [process.env.CHROME_PATH, "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"]) if (p && existsSync(p)) return p;
  throw new Error("no Chrome found: set CHROME_PATH");
}

async function waitFor(url: string, ms = 60000): Promise<void> {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 300));
  }
  throw new Error(`Vite did not answer at ${url}`);
}

let vite: ChildProcess | null = null;
try {
  vite = spawn("bunx", ["vite", "--host", "127.0.0.1", "--port", String(port), "--strictPort"], { cwd: root, stdio: ["ignore", "ignore", "inherit"] });
  const base = `http://127.0.0.1:${port}`;
  await waitFor(`${base}/studio.html`);
  const browser = await chromium.launch({
    executablePath: chromePath(),
    args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist", "--autoplay-policy=no-user-gesture-required"],
  });
  const page = await browser.newPage({ viewport: { width: 1100, height: 1950 } });
  page.on("pageerror", (e) => console.error("page:", e.message));
  await page.goto(`${base}/studio.html?id=${encodeURIComponent(id)}&capture=1&fps=${fps}`);
  await page.waitForFunction(() => window.studio || window.studioError, null, { timeout: 180000 });
  const err = await page.evaluate(() => window.studioError);
  if (err) throw new Error(err);
  const duration = await page.evaluate(() => window.studio!.duration);
  const seconds = Math.min(duration, opt("--seconds", duration));
  const frames = Math.ceil(seconds * fps);

  // the cover first (a moment two-fifths in, every robot busy); then the frames from the top
  const coverAt = opt("--cover-at", Math.min(seconds * 0.4, 20));
  await page.evaluate((t) => window.studio!.cover(t), coverAt);
  const cover = await page.evaluate(() => window.studio!.jpeg(0.95));
  writeFileSync(resolve(outDir, "cover.jpg"), Buffer.from(cover.split(",")[1], "base64"));
  await page.reload();
  await page.waitForFunction(() => window.studio, null, { timeout: 180000 });

  // frames in parallel chunks, each written as a numbered JPEG; then ONE encode of all of them
  // with the mix, so picture and sound share one clock (joining separately encoded pieces can
  // shift the sound by a frame or two at each join)
  const workers = Math.max(1, Number(process.env.RENDER_WORKERS ?? Math.floor(cpus().length / 2)));
  const per = Math.ceil(frames / workers);
  const frameDir = resolve(outDir, "frames");
  rmSync(frameDir, { recursive: true, force: true });
  mkdirSync(frameDir, { recursive: true });
  const t0 = Date.now();
  let doneFrames = 0;
  const chunk = async (w: number): Promise<void> => {
    const a = w * per, b = Math.min(frames, a + per);
    if (a >= b) return;
    const p = w === 0 ? page : await browser.newPage({ viewport: { width: 1100, height: 1950 } });
    if (w > 0) {
      p.on("pageerror", (e) => console.error("page:", e.message));
      await p.goto(`${base}/studio.html?id=${encodeURIComponent(id)}&capture=1&fps=${fps}`);
      await p.waitForFunction(() => window.studio, null, { timeout: 180000 });
    }
    // warm the faces up on the second before this chunk (their motion carries over frames)
    const warm = Math.max(0, a - fps);
    if (a > 0) await p.evaluate(([from, to]) => { for (let k = from; k < to; k++) window.studio!.frame(k); }, [warm, a]);
    for (let i = a; i < b; i++) {
      const jpg = await p.evaluate((k) => {
        window.studio!.frame(k);
        return window.studio!.jpeg(0.97);
      }, i);
      writeFileSync(resolve(frameDir, `${String(i).padStart(6, "0")}.jpg`), Buffer.from(jpg.split(",")[1], "base64"));
      if (++doneFrames % (fps * 5) === 0) console.log(`frame ${doneFrames}/${frames}  ${(doneFrames / ((Date.now() - t0) / 1000)).toFixed(2)} fps`);
    }
  };
  await Promise.all(Array.from({ length: workers }, (_, w) => chunk(w)));
  const mp4 = resolve(outDir, "reel.mp4");
  // loudness for Instagram: measure the mix, then normalise to -14 LUFS, -1 dBTP in one linear gain
  const mixPath = resolve(root, "public/shows", id, "mix.wav");
  const probe = spawnSync("ffmpeg", ["-hide_banner", "-t", String(seconds), "-i", mixPath, "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"], { encoding: "utf8" });
  const m = /\{[\s\S]*?\}/.exec(probe.stderr.slice(probe.stderr.lastIndexOf("[Parsed_loudnorm")));
  const meas = m ? JSON.parse(m[0]) : null;
  const norm = meas
    ? `loudnorm=I=-14:TP=-1:LRA=11:measured_I=${meas.input_i}:measured_TP=${meas.input_tp}:measured_LRA=${meas.input_lra}:measured_thresh=${meas.input_thresh}:offset=${meas.target_offset}:linear=true`
    : "loudnorm=I=-14:TP=-1:LRA=11";
  await new Promise<void>((ok, bad) => {
    const ff = spawn("ffmpeg", ["-y", "-loglevel", "error", "-framerate", String(fps), "-start_number", "0", "-i", resolve(frameDir, "%06d.jpg"),
      "-i", mixPath, "-map", "0:v", "-map", "1:a", "-t", String(seconds), "-af", norm,
      "-c:v", "libx264", "-preset", "slow", "-crf", "15", "-tune", "animation", "-pix_fmt", "yuv420p", "-profile:v", "high", "-r", String(fps), "-fps_mode", "cfr",
      "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart", mp4], { stdio: "inherit" });
    ff.on("close", (c) => (c === 0 ? ok() : bad(new Error(`ffmpeg exited ${c}`))));
  });
  rmSync(frameDir, { recursive: true, force: true });
  await browser.close();
  console.log(`wrote ${mp4} and cover.jpg (${frames} frames in ${((Date.now() - t0) / 1000).toFixed(0)} s)`);
} finally {
  vite?.kill();
}
