"""Minimal subscriber of the opensonar service: prints every ping of one head.

    opensonar server 192.168.1.45 --head 0 &        # or: python tools/sonar_emulator.py FILE.son & opensonar server 127.0.0.1
    python examples/subscriber.py --head 0
"""
import argparse

from opensonar.client import Subscriber

ap = argparse.ArgumentParser()
ap.add_argument("--address", default="tcp://127.0.0.1:4546")
ap.add_argument("--head", type=int, help="only this head (default: all)")
args = ap.parse_args()

with Subscriber(args.address, head=args.head) as sub:
    for msg in sub:
        p = msg.ping
        print(f"{msg.meta['utc']}  {msg.topic}  ping {p.number}  {p.range_start:.2f}–{p.range_stop:.2f} m  "
              f"{len(p.signals)}×{p.signals[0].size} samples" + (f"  image {msg.image.shape}" if msg.image is not None else ""))
