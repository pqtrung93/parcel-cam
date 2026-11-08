#!/usr/bin/env python3
"""Self-check for the motion detector. No camera needed: synthetic grey frames.

  python3 test_cam.py
"""
import cam

N = cam.W * cam.H
still = bytes([120]) * N
# 2% of the frame flipped to white = a person walking through
moved = bytearray(still)
for i in range(int(N * 0.02)):
    moved[i] = 240
moved = bytes(moved)

assert cam.motion_frac(still, still) == 0.0, "identical frames must score 0"
frac = cam.motion_frac(moved, still)
assert 0.015 < frac < 0.025, f"2% of pixels moved, got {frac:.4f}"
# a 1-level global shift (sensor noise / auto-exposure) must not register
noisy = bytes(min(255, b + 3) for b in still)
assert cam.motion_frac(noisy, still) == 0.0, "sub-threshold noise must not trigger"
assert frac > cam.MOTION_FRAC, f"2% motion ({frac:.3f}) must clear MOTION_FRAC ({cam.MOTION_FRAC})"

# --- a lost picture is not motion -------------------------------------------------------
# A flat frame is what ffmpeg emits when the link drops: solid black, or a mid-grey
# placeholder. Compared against the last real picture it measures 100% "changed", which fired
# an alert every time the camera blinked.
flat_black = bytes([0]) * N
flat_grey = bytes([128]) * N
assert cam.dead(flat_black), "a solid black frame must be rejected as a lost signal"
assert cam.dead(flat_grey), "the grey placeholder must be rejected too"
# A uniform but lit wall still has no contrast — that reads as dead, which is the known
# ceiling of a contrast test (see the note in cam.dead). Asserted so the behaviour is pinned.
assert cam.dead(still), "a contrastless frame reads as dead signal by design"
# A real night scene has contrast and must survive: bright IR-lit ground, dark sky.
night = bytearray(N)
for i in range(N):
    night[i] = 30 if i % 320 < 160 else 180
assert not cam.dead(bytes(night)), "a dark frame with contrast is a picture, not a dead link"

# --- dense motion, not one tall spike ---------------------------------------------------
hits = []
assert not cam.fires(hits, now=100.0), "no hits: nothing fires"
# One isolated spike: a link recovering reads as 100% for a single frame.
hits.append(100.0)
assert not cam.fires(hits, now=100.0, frames=2, window=2.0), \
    "a single hot frame must not fire, however large"
# A second one 4 s later is a separate event, not density.
hits.append(104.0)
assert not cam.fires(hits, now=104.0, frames=2, window=2.0), \
    "two spikes 4 s apart must not fire — that is noise, not a person"
# Two close together is what a person walking gives.
hits[:] = [104.0, 104.4]
assert cam.fires(hits, now=104.4, frames=2, window=2.0), "two hits inside the window must fire"

# --- pruning keeps the list bounded -----------------------------------------------------
hits[:] = [0.0, 0.4, 100.0, 100.4]
cam.fires(hits, now=100.4, frames=2, window=2.0)
assert hits == [100.0, 100.4], f"old hits must be pruned, got {hits}"

print(f"motion_frac OK: still=0.000 noise=0.000 person={frac:.3f} > {cam.MOTION_FRAC}")
print(f"dead FRAME OK: black=True grey=True flatwall=True night-contrast=False "
      f"(DEAD_SPREAD={cam.DEAD_SPREAD})")
print(f"fires OK: 1 spike=no, 2 spikes 4s apart=no, 2 close=yes "
      f"({cam.MOTION_FRAMES}/{cam.MOTION_WINDOW:.0f}s)")
