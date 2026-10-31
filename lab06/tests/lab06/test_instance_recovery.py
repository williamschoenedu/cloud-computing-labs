"""Verify automatic replacement of the Weather instance."""

import os
import time

import boto3
import pytest
import requests

from stack.config import load_dotenv
from stack.weather_config import load_settings


@pytest.mark.chaos
@pytest.mark.skipif(
    os.getenv("RUN_CHAOS") != "1",
    reason="Requires explicitly enabled live AWS chaos test.",
)
def test_weather_instance_recovery():
    """Observe advisor failure and recovery through a new healthy target."""
    settings = load_settings()
    outputs = load_dotenv("stack_outputs.env")
    session = boto3.Session(region_name=settings.region)
    elb = session.client("elbv2")
    ec2 = session.client("ec2")
    arn = outputs["WEATHER_TG_ARN"]
    targets = elb.describe_target_health(
        TargetGroupArn=arn
    )["TargetHealthDescriptions"]
    healthy = [
        t for t in targets if t["TargetHealth"]["State"] == "healthy"
    ]
    assert len(healthy) == 1
    old = healthy[0]["Target"]["Id"]
    url = f'http://{outputs["ALB_DNS_NAME"]}:8081/advice?zip=60616'
    assert requests.get(url, timeout=30).status_code == 200

    started = time.monotonic()
    ec2.terminate_instances(InstanceIds=[old])
    print("Terminated Weather instance:", old, flush=True)
    saw_502 = False
    recovered = False

    while time.monotonic() - started < 300:
        remaining = 300 - (time.monotonic() - started)
        try:
            response = requests.get(url, timeout=min(25, remaining))
            status = response.status_code
            saw_502 = saw_502 or status == 502
        except requests.RequestException:
            status = "connection timeout/error"
        elapsed = time.monotonic() - started
        print(f"{elapsed:.1f}s | Advisor: {status}", flush=True)

        targets = elb.describe_target_health(
            TargetGroupArn=arn
        )["TargetHealthDescriptions"]
        replacements = [
            t["Target"]["Id"] for t in targets
            if t["Target"]["Id"] != old
            and t["TargetHealth"]["State"] == "healthy"
        ]
        if replacements and status == 200:
            recovered = time.monotonic() - started <= 300
            print("Healthy replacement:", replacements, flush=True)
            break
        time.sleep(min(5, max(0, 300 - (time.monotonic() - started))))

    assert recovered, "Advisor did not recover within 300 seconds."
    assert saw_502, "No temporary Advisor HTTP 502 was observed."
