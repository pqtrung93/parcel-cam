#!/usr/bin/env python3
"""Parcel camera: motion -> photo + clip -> Telegram.

  python3 cam.py            # watch
  python3 cam.py --test     # one photo + one 5 s clip, sent to Telegram
  python3 cam.py --calib N  # print motion scores for N seconds, send nothing

Why frame-diff and not ONVIF: this firmware (IPC-A42P-D-V3) answers Subscribe but
only ever pushes PropertyOperation="Initialized" State=false — never MotionAlarm —
and PullPoint answers "Subscribe Creation Failed". So motion comes from the video
itself: ffmpeg decodes the 640x480 sub stream to 320x240 grey at 2 fps and we count
pixels that moved. Measured noise on a static scene: <0.1% of pixels.
"""
import os, select, subprocess, sys, time
from datetime import datetime
from pathlib import Path

CAM_IP = os.environ.get("CAM_IP", "")
CAM_USER = os.environ.get("CAM_USER", "admin")
CAM_PASS = os.environ.get("CAM_PASS", "")
CLIP_SECONDS = int(os.environ.get("CLIP_SECONDS", "10"))
COOLDOWN = int(os.environ.get("COOLDOWN", "90"))
KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "14"))
PIXEL_DELTA = int(os.environ.get("PIXEL_DELTA", "25"))       # grey levels that count as "changed"
MOTION_FRAC = float(os.environ.get("MOTION_FRAC", "0.01"))  # 1% of the frame moved
MOTION_FRAMES = int(os.environ.get("MOTION_FRAMES", "2"))   # hits needed inside MOTION_WINDOW
# How many hits must land inside MOTION_WINDOW seconds. A window rather than a strict
# consecutive run because real motion at the threshold flickers across it, while a dropped
# link gives one isolated spike and recovers.
MOTION_WINDOW = float(os.environ.get("MOTION_WINDOW", "2.0"))
# Contrast a frame needs before it counts as a picture. A flat frame means the link dropped or
# the decoder had no reference frame; compared against the last real picture it lights up the
# whole screen and reads as 100% motion. Measured on the parcel cam at night: a real scene
# spreads well over 100 grey levels, a dead frame is flat.
DEAD_SPREAD = int(os.environ.get("DEAD_SPREAD", "16"))
# Longest the stream may go silent before it is treated as a dead link. Normal cadence is
# 2 fps; 15 s tolerates a camera stall without freezing the watcher forever.
FRAME_TIMEOUT = float(os.environ.get("FRAME_TIMEOUT", "15"))
TG_TOKEN = os.environ.get("TG_TOKEN", "")
TG_CHAT = os.environ.get("TG_CHAT", "")
CLIPS = Path(os.environ.get("CLIPS_DIR", "/dev/shm/parcel-cam"))   # tmpfs = RAM, wiped on reboot
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")
W, H = 320, 240

RTSP = (f"rtsp://{CAM_USER}:{CAM_PASS.replace('@', '%40').replace(':', '%3A')}"
        f"@{CAM_IP}:554/cam/realmonitor?channel=1&subtype={{sub}}")


def grab_photo(path, width=1920):
    p = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-rtsp_transport", "tcp",
                        "-i", RTSP.format(sub=0), "-frames:v", "1",
                        "-vf", f"scale={width}:-2", "-q:v", "2", str(path)],
                       capture_output=True, text=True, timeout=45)
    return p.returncode == 0 and path.exists() and path.stat().st_size > 5000


def record(path, seconds=None):
    """720p h264: the camera emits HEVC, which Telegram's inline player chokes on.
    ~11 s wall for a 10 s clip, inside COOLDOWN."""
    seconds = seconds or CLIP_SECONDS          # read at call time, not at def time
    p = subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-rtsp_transport", "tcp",
                        "-i", RTSP.format(sub=0), "-t", str(seconds), "-an",
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "26", "-movflags", "+faststart", str(path)],
                       capture_output=True, text=True, timeout=seconds + 90)
    return p.returncode == 0 and path.exists() and path.stat().st_size > 20000


def tg(method, path, caption, thumb=None):
    if not TG_TOKEN:
        print("[tg] no TG_TOKEN, kept locally:", path, flush=True)
        return False
    field = "video" if path.suffix == ".mp4" else "photo"
    cmd = ["curl", "-sS", "--max-time", "180", "-F", f"chat_id={TG_CHAT}",
           "-F", f"caption={caption}", "-F", f"{field}=@{path}"]
    if thumb:
        cmd += ["-F", f"thumbnail=@{thumb}"]
    p = subprocess.run(cmd + [f"https://api.telegram.org/bot{TG_TOKEN}/{method}"],
                       capture_output=True, text=True, timeout=190)
    ok = '"ok":true' in p.stdout
    print(f"[tg] {field} {'ok' if ok else 'FAIL ' + (p.stdout or p.stderr)[:200]}", flush=True)
    return ok


def prune():
    cutoff = time.time() - KEEP_DAYS * 86400
    for f in CLIPS.iterdir():
        if f.is_file() and f.stat().st_mtime < cutoff:
            f.unlink(missing_ok=True)


def motion_frac(cur, prev):
    return sum(1 for a, b in zip(cur, prev) if abs(a - b) > PIXEL_DELTA) / len(cur)


def dead(frame):
    """True when the frame carries no picture at all — a dropped link, not a dark yard.

    When the RTSP link drops or the decoder has no reference frame, ffmpeg emits a flat
    picture. Compared against the last real frame it lights up every pixel and reads as 100%
    of the screen moving. Deliberately not a brightness test: a night frame still has IR-lit
    contrast and passes.

    ponytail: a genuine scene with no contrast anywhere (a lens covered by a hand, a totally
    blank wall filling the frame) also reads as dead and is skipped. Distinguishing "camera
    blinded" from "link dropped" needs a per-pixel temporal check, not a per-frame one — add
    it if you ever need an alert for tampering.
    """
    return max(frame) - min(frame) < DEAD_SPREAD


def stream_frames():
    """Yield 320x240 grey frames at 2 fps from the sub stream."""
    n = W * H
    proc = subprocess.Popen([FFMPEG, "-loglevel", "error", "-rtsp_transport", "tcp",
                             "-i", RTSP.format(sub=1), "-an", "-vf", f"fps=2,scale={W}:{H}",
                             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            # ponytail: read(n) blocks forever if ffmpeg stalls while the RTSP socket stays
            # open — this camera does exactly that, and the whole watcher froze for 7 days with
            # no exception and no log. A select timeout turns a stall into a clean restart.
            if not select.select([proc.stdout], [], [], FRAME_TIMEOUT)[0]:
                print("[signal] no frame for %.0fs, restarting stream" % FRAME_TIMEOUT,
                      flush=True)
                return
            buf = proc.stdout.read(n)
            if len(buf) < n:
                return
            yield buf
    finally:
        proc.kill()


def handle(seconds=None):
    """Record clip into tmpfs, upload it with a caption (and a frame grabbed as
    thumbnail), delete the file the moment the upload succeeds. A failed upload
    keeps the file on disk so nothing is lost."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    jpg, mp4 = CLIPS / f"{stamp}.jpg", CLIPS / f"{stamp}.mp4"
    when = datetime.now()
    if record(mp4, seconds):
        grab_photo(jpg)                     # thumbnail only, never sent on its own
        caption = f"📦 Gói hàng — chuyển động {when:%H:%M:%S %d/%m/%Y}"
        if tg("sendVideo", mp4, caption, thumb=jpg if jpg.exists() else None):
            mp4.unlink(missing_ok=True)
            jpg.unlink(missing_ok=True)
    prune()


def fires(hits, now, frames=None, window=None):
    """Prune `hits` to the window and report whether enough landed inside it.

    Extracted so the one non-trivial branch is testable without a camera: `hits` is a list of
    timestamps, mutated in place to stay bounded. Dense motion, not one tall spike — a single
    frame at 100% (a link recovering) can never satisfy MOTION_FRAMES >= 2.
    """
    frames = MOTION_FRAMES if frames is None else frames
    window = MOTION_WINDOW if window is None else window
    hits[:] = [t for t in hits if now - t <= window]
    return len(hits) >= frames


def watch():
    CLIPS.mkdir(parents=True, exist_ok=True)
    print(f"[cam] watching {CAM_IP} frac>{MOTION_FRAC} "
          f"x{MOTION_FRAMES}/{MOTION_WINDOW:.0f}s cooldown={COOLDOWN}s", flush=True)
    prev, last = None, 0.0
    hits = []                # timestamps of recent frames above MOTION_FRAC
    dead_run = 0
    while True:
        warm = 3             # the first frames after a (re)connect are an auto-exposure settle
        try:
            for cur in stream_frames():
                # A lost picture is not motion: skip the frame AND drop the reference, so the
                # next real frame is not compared against the one from before the outage.
                if dead(cur):
                    prev = None
                    dead_run += 1
                    hits.clear()
                    continue
                if dead_run:
                    print(f"[signal] {dead_run} flat frame(s) ignored", flush=True)
                    dead_run = 0
                if prev is None:
                    prev = cur
                    continue
                # ponytail: a flat skip or a reconnect re-enters here; the first frames after
                # one are an exposure/reference settle and measure 4-6% on a still scene.
                if warm:
                    warm -= 1
                    prev = cur
                    continue
                frac = motion_frac(cur, prev)
                prev = cur
                now = time.time()
                if frac > MOTION_FRAC:
                    hits.append(now)
                if not fires(hits, now) or now - last < COOLDOWN:
                    continue
                last, hits = now, []
                print(f"[motion] frac={frac:.3f} hits={MOTION_FRAMES}/{MOTION_WINDOW:.0f}s",
                      flush=True)
                handle()
        except Exception as e:
            print("[loop]", type(e).__name__, str(e)[:160], flush=True)
        time.sleep(5)                       # stream died: camera reboot or network blip


def calib(seconds):
    prev, scores, start = None, [], time.time()
    for cur in stream_frames():
        if prev is not None:
            scores.append(motion_frac(cur, prev))
        prev = cur
        if time.time() - start > seconds:
            break
    scores.sort()
    if scores:
        print(f"frames={len(scores)} min={scores[0]:.4f} p50={scores[len(scores)//2]:.4f} "
              f"p99={scores[int(len(scores) * .99)]:.4f} max={scores[-1]:.4f}")
        print("suggest MOTION_FRAC =", round(max(scores[-1] * 3, 0.003), 3))


if __name__ == "__main__":
    if "--test" in sys.argv:
        CLIPS.mkdir(parents=True, exist_ok=True)
        CLIP_SECONDS = 5                    # same code path as production, shorter clip
        handle(seconds=5)
    elif "--calib" in sys.argv:
        calib(int(sys.argv[sys.argv.index("--calib") + 1]))
    else:
        watch()
