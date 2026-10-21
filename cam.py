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
import os, subprocess, sys, time
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
MOTION_FRAMES = int(os.environ.get("MOTION_FRAMES", "2"))   # consecutive hits to fire
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


def tg(method, path, caption):
    if not TG_TOKEN:
        print("[tg] no TG_TOKEN, kept locally:", path, flush=True)
        return False
    field = "video" if path.suffix == ".mp4" else "photo"
    p = subprocess.run(["curl", "-sS", "--max-time", "180", "-F", f"chat_id={TG_CHAT}",
                        "-F", f"caption={caption}", "-F", f"{field}=@{path}",
                        f"https://api.telegram.org/bot{TG_TOKEN}/{method}"],
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


def frames():
    """Yield 320x240 grey frames at 2 fps from the sub stream."""
    n = W * H
    proc = subprocess.Popen([FFMPEG, "-loglevel", "error", "-rtsp_transport", "tcp",
                             "-i", RTSP.format(sub=1), "-an", "-vf", f"fps=2,scale={W}:{H}",
                             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            buf = proc.stdout.read(n)
            if len(buf) < n:
                return
            yield buf
    finally:
        proc.kill()


def handle(seconds=None):
    """Grab photo + clip into tmpfs, upload both, delete each file the moment its
    upload succeeds. A failed upload keeps the file on disk so nothing is lost."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    jpg, mp4 = CLIPS / f"{stamp}.jpg", CLIPS / f"{stamp}.mp4"
    when = datetime.now()
    if grab_photo(jpg) and tg("sendPhoto", jpg, f"📦 Gói hàng — chuyển động {when:%H:%M:%S %d/%m/%Y}"):
        jpg.unlink(missing_ok=True)
    if record(mp4, seconds) and tg("sendVideo", mp4, f"🎬 {seconds or CLIP_SECONDS}s @ {when:%H:%M:%S %d/%m/%Y}"):
        mp4.unlink(missing_ok=True)
    prune()


def watch():
    CLIPS.mkdir(parents=True, exist_ok=True)
    print(f"[cam] watching {CAM_IP} frac>{MOTION_FRAC} cooldown={COOLDOWN}s", flush=True)
    prev, hits, last = None, 0, 0.0
    while True:
        try:
            for cur in frames():
                if prev is None:
                    prev = cur
                    continue
                frac, prev = motion_frac(cur, prev), cur
                hits = hits + 1 if frac > MOTION_FRAC else 0
                if hits < MOTION_FRAMES or time.time() - last < COOLDOWN:
                    continue
                last, hits = time.time(), 0
                print(f"[motion] frac={frac:.3f}", flush=True)
                handle()
        except Exception as e:
            print("[loop]", type(e).__name__, str(e)[:160], flush=True)
        time.sleep(5)                       # stream died: camera reboot or network blip


def calib(seconds):
    prev, scores, start = None, [], time.time()
    for cur in frames():
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
