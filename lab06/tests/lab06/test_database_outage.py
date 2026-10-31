"""Verify weather availability during a temporary database outage."""

import os
import subprocess
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
def test_weather_survives_database_outage():
    """Restore the database rule even when the outage check fails."""
    settings = load_settings()
    v = settings.values
    db = load_dotenv("db_outputs.env")
    outputs = load_dotenv("stack_outputs.env")
    session = boto3.Session(region_name=settings.region)
    ec2 = session.client("ec2")
    elb = session.client("elbv2")
    target = elb.describe_target_health(
        TargetGroupArn=outputs["WEATHER_TG_ARN"]
    )["TargetHealthDescriptions"][0]["Target"]["Id"]
    instance = ec2.describe_instances(
        InstanceIds=[target]
    )["Reservations"][0]["Instances"][0]
    host = instance["PublicIpAddress"]
    url = f'http://{outputs["ALB_DNS_NAME"]}:8080/weather?zip=60616'
    rule = {
        "IpProtocol": "tcp",
        "FromPort": int(v["DB_PORT"]),
        "ToPort": int(v["DB_PORT"]),
        "UserIdGroupPairs": [{"GroupId": v["APP_SECURITY_GROUP_ID"]}],
    }

    def restart():
        subprocess.run(
            [
                "ssh", "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=accept-new",
                "-i", f'/{v["KEY_NAME"]}.pem',
                f"ec2-user@{host}",
                "sudo systemctl restart weather.service",
            ],
            check=True, timeout=30,
        )
        time.sleep(3)

    ec2.revoke_security_group_ingress(
        GroupId=db["DB_SECURITY_GROUP_ID"], IpPermissions=[rule]
    )
    try:
        restart()
        response = requests.get(url, timeout=40)
        print("During outage:", response.status_code, response.text)
        assert response.status_code == 200
        assert response.json()["cache"] == "unavailable"
        assert response.json()["forecast"]
    finally:
        ec2.authorize_security_group_ingress(
            GroupId=db["DB_SECURITY_GROUP_ID"], IpPermissions=[rule]
        )
        print("Database network rule restored.")
        restart()

    response = requests.get(url, timeout=40)
    print("After recovery:", response.status_code, response.text)
    assert response.status_code == 200
    assert response.json()["cache"] in ("miss", "hit")
