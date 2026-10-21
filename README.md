# parcel-cam — parcel camera

Watches an IP camera on the LAN. On motion: grab a photo + record a clip, send
both to a Telegram group. No cloud, no quota, nothing written to disk.

[Tiếng Việt](README.vi.md)

## How it works

```
camera --RTSP--> ffmpeg (sub stream 320x240 grey @2fps)
                    |
                    v
          count pixels changed > PIXEL_DELTA --> above MOTION_FRAC for N frames
                    |
                    v
        1920px photo (main stream) + 720p h264 clip  -->  Telegram group
                    |
                    v
                delete each file once its upload succeeds (tmpfs)
```

- **Motion comes from the video, not ONVIF events.** Imou consumer firmware
  answers `Subscribe` but only ever pushes `PropertyOperation="Initialized"
  State=false` — never `MotionAlarm` — and PullPoint replies `Subscribe Creation
  Failed`. Tried and discarded.
- **COOLDOWN** stops notification storms: 90 s minimum between sends by default.
- Clips are **re-encoded to h264**: the camera emits HEVC, which Telegram will
  not play inline.

## Install

```bash
cp .env.example .env && nano .env     # CAM_IP / CAM_PASS / TG_TOKEN / TG_CHAT
python3 test_cam.py                   # self-check, no camera needed
python3 cam.py --calib 40             # measure background noise, suggests MOTION_FRAC
python3 cam.py --test                 # send one photo + one 5 s clip
```

Run as a service:

```bash
systemctl link "$PWD/parcel-cam.service"
systemctl enable --now parcel-cam
journalctl -u parcel-cam -f
```

## Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `CAM_IP`, `CAM_USER`, `CAM_PASS` | RTSP camera (Dahua/Imou: user `admin` + the account password) |
| `TG_TOKEN`, `TG_CHAT` | bot token + chat/group id (groups are negative, e.g. `-100…`) |
| `CLIP_SECONDS` | clip length, default 10 |
| `COOLDOWN` | minimum seconds between two sends, default 90 |
| `MOTION_FRAC` | fraction of pixels that must change, default 0.008 |
| `MOTION_FRAMES` | consecutive frames above threshold before firing, default 2 |
| `PIXEL_DELTA` | grey levels that count as "changed", default 25 |
| `KEEP_DAYS` | delete files left behind by failed uploads after N days, default 14 |
| `CLIPS_DIR` | scratch dir, default `/dev/shm/parcel-cam` (RAM) |

Dahua/Imou RTSP path:

```
rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0
```

`subtype 0` = main stream (2560x1440 HEVC @25), `subtype 1` = sub stream (640x480 @15).

## Tuning the threshold

Every camera and scene has its own background noise. Measure a static scene for 60 s:

```
frames=116 p50 0.0005 p90 0.0010 p99 0.0026 max 0.0039
```

→ set `MOTION_FRAC` at roughly twice the max (0.008). Missing people walking
past: drop to 0.004. False alarms from sunlight or a light switching: raise to 0.02.

## Files

```
cam.py               watcher + Telegram upload (all the logic)
test_cam.py          motion_frac self-check, no camera needed
parcel-cam.service   systemd unit (EnvironmentFile=.env, Restart=always)
.env.example         config template
```

## Storage

Photos and clips go to **tmpfs** (`/dev/shm/parcel-cam` = RAM, lost on reboot).
Each file is deleted the moment its upload succeeds. A failed upload leaves the
file behind to be sent later; `prune()` clears leftovers after `KEEP_DAYS` if
Telegram stays down for days. Nothing is written to disk.

## Security

- `.env` is gitignored and must never be committed. The repo ships only
  `.env.example` with empty values.
- A leaked bot token means a lost bot: revoke it via `/revoke` with @BotFather,
  then update `.env`.
- A leaked camera password or safety code: change it in Imou Life → Device Settings.

## Operations notes

- A 10 s clip costs ~11 s of CPU to encode (veryfast, 720p) — inside COOLDOWN,
  so sends never pile up.
- Photos come from the main stream and already carry the camera's timestamp and logo.
- Camera reboot or network drop: the loop reopens RTSP after 5 s; the unit is
  `Restart=always`.
- Quick health check: `systemctl is-active parcel-cam` and
  `ls /dev/shm/parcel-cam` (should be empty after each send).
