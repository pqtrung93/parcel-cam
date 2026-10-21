# parcel-cam — camera gói hàng

Giám sát camera IP trong LAN. Có chuyển động → chụp 1 ảnh + quay 1 clip → gửi
lên Telegram group. Không dùng cloud, không tốn quota, không ghi gì xuống đĩa.

## Cách hoạt động

```
camera --RTSP--> ffmpeg (sub stream 320x240 grey @2fps)
                    |
                    v
            đếm pixel đổi > PIXEL_DELTA  --> vượt MOTION_FRAC N frame liên tiếp
                    |
                    v
        ảnh 1920px (stream chính) + clip 720p h264  -->  Telegram group
                    |
                    v
              upload xong thì xoá file (tmpfs)
```

- **Phát hiện chuyển động bằng chính video**, không dùng ONVIF event: firmware
  Imou consumer trả lời `Subscribe` nhưng chỉ push `PropertyOperation="Initialized"
  State=false`, không bao giờ có `MotionAlarm`; PullPoint thì `Subscribe Creation
  Failed`. Đã thử và loại.
- **COOLDOWN** chặn bão thông báo: mặc định 90s giữa 2 lần gửi.
- Clip phải **re-encode sang h264**: camera xuất HEVC, Telegram không phát inline.

## Cài đặt

```bash
cp .env.example .env && nano .env     # điền CAM_IP / CAM_PASS / TG_TOKEN / TG_CHAT
python3 test_cam.py                   # self-check, không cần camera
python3 cam.py --calib 40             # đo nhiễu nền, in MOTION_FRAC gợi ý
python3 cam.py --test                 # gửi thử 1 ảnh + 1 clip 5s
```

Chạy thường trực:

```bash
systemctl link "$PWD/parcel-cam.service"
systemctl enable --now parcel-cam
journalctl -u parcel-cam -f
```

## Cấu hình (`.env`)

| Biến | Ý nghĩa |
|---|---|
| `CAM_IP`, `CAM_USER`, `CAM_PASS` | camera RTSP (Dahua/Imou: user `admin` + mật khẩu tài khoản) |
| `TG_TOKEN`, `TG_CHAT` | bot token + chat/group id (group thì id âm, vd `-100…`) |
| `CLIP_SECONDS` | độ dài clip, mặc định 10 |
| `COOLDOWN` | giây tối thiểu giữa 2 lần gửi, mặc định 90 |
| `MOTION_FRAC` | tỉ lệ pixel phải đổi để tính là chuyển động, mặc định 0.008 |
| `MOTION_FRAMES` | số frame liên tiếp vượt ngưỡng mới bắn, mặc định 2 |
| `PIXEL_DELTA` | mức xám tối thiểu coi là "đổi", mặc định 25 |
| `KEEP_DAYS` | xoá file tồn (upload fail) sau N ngày, mặc định 14 |
| `CLIPS_DIR` | nơi ghi tạm, mặc định `/dev/shm/parcel-cam` (RAM) |

RTSP path của Dahua/Imou:

```
rtsp://<user>:<pass>@<ip>:554/cam/realmonitor?channel=1&subtype=0
```

`subtype 0` = stream chính (2560x1440 HEVC @25), `subtype 1` = phụ (640x480 @15).

## Hiệu chỉnh ngưỡng

Mỗi camera/khung hình có nhiễu nền khác nhau. Đo trên cảnh tĩnh 60s:

```
frames=116 p50 0.0005 p90 0.0010 p99 0.0026 max 0.0039
```

→ đặt `MOTION_FRAC` cao hơn max ~2 lần (0.008). Nếu bỏ sót người đi qua, hạ
xuống 0.004; nếu báo giả vì nắng/đèn, tăng lên 0.02.

## File

```
cam.py               watcher + gửi Telegram (toàn bộ logic)
test_cam.py          self-check motion_frac, không cần camera
parcel-cam.service   systemd unit (EnvironmentFile=.env, Restart=always)
.env.example         mẫu cấu hình
```

## Lưu trữ

Ảnh/clip ghi vào **tmpfs** (`/dev/shm/parcel-cam` = RAM, mất khi reboot).
Upload xong file nào xoá ngay file đó. Upload lỗi thì file ở lại để gửi bù lần
sau; `prune()` dọn theo `KEEP_DAYS` nếu Telegram chết dài ngày. Không ghi gì
xuống đĩa.

## Bảo mật

- `.env` nằm trong `.gitignore`, không bao giờ commit. Repo chỉ có `.env.example`
  với giá trị rỗng.
- Token bot lộ = mất bot: thu hồi bằng `/revoke` với @BotFather rồi cập nhật `.env`.
- Mật khẩu/safety code camera lộ = đổi trong Imou Life → Device Settings.

## Ghi chú vận hành

- 10s clip ≈ 11s CPU encode (veryfast, 720p) — nằm trong COOLDOWN nên không dồn.
- Ảnh chụp từ stream chính, đã có timestamp + logo của camera in sẵn.
- Camera reboot hay rớt mạng: vòng lặp tự mở lại RTSP sau 5s, service `Restart=always`.
- Kiểm tra nhanh: `systemctl is-active parcel-cam`, `ls /dev/shm/parcel-cam`
  (bình thường phải rỗng sau mỗi lần gửi).
