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
print(f"motion_frac OK: still=0.000 noise=0.000 person={frac:.3f} > {cam.MOTION_FRAC}")
