#!/usr/bin/env python3
"""Terminate an ASG instance and measure HTTP recovery time."""

import argparse
import csv
import sys
import time

from aws_helpers import (
    get_inservice_instance_id,
    terminate_instance_via_asg,
)
from http_probe import probe_once


def load_env(path: str) -> dict:
    values = {}

    with open(path) as env_file:
        for line in env_file:
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()

    return values


def main():
    parser = argparse.ArgumentParser(
        description="Measure recovery after terminating an ASG instance."
    )
    parser.add_argument(
        "--tier",
        choices=["uploader", "viewer"],
        required=True,
    )
    parser.add_argument("--slo-seconds", type=float)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--max-wait", type=float, default=600.0)
    args = parser.parse_args()

    env = {
        **load_env("/root/.env"),
        **load_env("/root/stack_outputs.env"),
    }

    region = env.get("AWS_REGION", "us-east-2")
    alb_dns = env.get("ALB_DNS_NAME")

    if not alb_dns:
        sys.exit("ALB_DNS_NAME was not found.")

    slo_seconds = (
        args.slo_seconds
        if args.slo_seconds is not None
        else float(env.get("RECOVERY_SLO_SECONDS", 180))
    )

    if args.tier == "uploader":
        port = env.get("UPLOADER_LISTENER_PORT", "8080")
        asg_name = env.get("UPLOADER_ASG_NAME")
    else:
        port = env.get("VIEWER_LISTENER_PORT", "8081")
        asg_name = env.get("VIEWER_ASG_NAME")

    url = f"http://{alb_dns}:{port}/"

    print(f"Target tier: {args.tier}")
    print(f"Endpoint: {url}")
    print(f"Recovery SLO: {slo_seconds:.0f}s")
    print("Confirming that the endpoint is currently healthy...")

    if not probe_once(url):
        sys.exit("Endpoint is not healthy. Chaos test cancelled.")

    instance_id = get_inservice_instance_id(asg_name, region)

    if not instance_id:
        sys.exit(f"No InService instance found in {asg_name}.")

    print(f"Current instance: {instance_id}")
    print("Terminating it through the Auto Scaling Group...")

    started_at = time.monotonic()
    terminate_instance_via_asg(instance_id, region)

    rows = []
    outage_seen = False
    recovered_at = None

    while True:
        elapsed = time.monotonic() - started_at

        if elapsed > args.max_wait:
            break

        is_up = probe_once(url)
        rows.append((round(elapsed, 2), is_up))

        state = "UP" if is_up else "DOWN"
        print(f"{elapsed:6.1f}s  {state}")

        if not is_up:
            outage_seen = True

        if outage_seen and is_up:
            recovered_at = elapsed
            break

        time.sleep(args.poll_interval)

    csv_path = (
        f"/root/chaos_test_{args.tier}_{int(time.time())}.csv"
    )

    with open(csv_path, "w", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(["elapsed_seconds", "is_up"])
        writer.writerows(rows)

    print(f"Poll log written to {csv_path}")

    if recovered_at is None:
        print(
            f"FAIL: endpoint did not recover within "
            f"{args.max_wait:.0f}s."
        )
        sys.exit(1)

    print(f"Recovery time: {recovered_at:.1f}s")

    if recovered_at <= slo_seconds:
        print("PASS: recovery time met the SLO.")
        sys.exit(0)

    print("FAIL: recovery time exceeded the SLO.")
    sys.exit(1)


if __name__ == "__main__":
    main()
