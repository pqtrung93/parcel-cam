# parcel-cam — camera gói hàng (Imou IPC-A42P-D-V3)

Giám sát camera qua LAN, chuyển động → gửi ảnh + clip lên Telegram.

## Thiết bị

Điền trong `.env` (không commit): `CAM_IP`, `CAM_USER`, `CAM_PASS`, `TG_TOKEN`, `TG_CHAT`.
Camera Imou/Dahua đời IPC-A42, RTSP path:

    rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0

subtype 0 = stream chính (2560x1440 HEVC @25), subtype 1 = phụ (640x480 @15).

## Chạy

    cp .env.example .env && nano .env      # CAM_PASS + TG_TOKEN
    python3 test_cam.py                    # self-check, không cần camera
    python3 cam.py --calib 40              # đo nhiễu nền, in MOTION_FRAC gợi ý
    python3 cam.py --test                  # 1 ảnh + 1 clip 5s vào Telegram
    python3 cam.py                         # chạy thật (systemd)

    systemctl link /mnt/blockstore/parcel-cam/parcel-cam.service
    systemctl enable --now parcel-cam

## Cách phát hiện chuyển động

Không dùng ONVIF: firmware này trả lời `Subscribe` nhưng chỉ push
`PropertyOperation="Initialized" State=false` — không bao giờ có `MotionAlarm`,
và PullPoint trả `Subscribe Creation Failed`. Nên chuyển động lấy từ chính video:
ffmpeg decode sub stream → 320x240 grey @2fps → đếm pixel đổi >25 mức xám.
Nhiễu nền đo được p50 = 0.1%, max 4% (một lần auto-exposure); `MOTION_FRAC` mặc
định 1% + 2 frame liên tiếp.

## File

    cam.py        watcher + gửi Telegram
    test_cam.py   self-check motion_frac (không cần camera)
    parcel-cam.service  systemd unit

## Lưu trữ

Clip/ảnh ghi vào **`/dev/shm/parcel-cam`** (tmpfs = RAM, mất khi reboot), upload
xong file nào xoá ngay file đó. Upload lỗi thì file ở lại để lần sau gửi bù —
`prune()` vẫn xoá theo `KEEP_DAYS` nếu Telegram chết dài ngày. Không ghi gì
xuống đĩa.

## Ghi chú

- Clip gửi đi là **720p h264**: camera xuất HEVC, Telegram không phát inline được.
  10s clip ≈ 11s CPU encode, nằm trong COOLDOWN 90s.
- Ảnh chụp từ stream chính 1920px, có timestamp + logo imou in sẵn.
- Giữ clip 14 ngày (`KEEP_DAYS`), xoá theo mtime.
