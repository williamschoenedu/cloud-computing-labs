"""Lab06 roles, runtime configuration and idempotent launch templates."""

import base64
import json

from .config import Tier
from .policies import trust_policy
from .util import ignore_errors
from .compute import launch_template_exists


def configure_tiers(settings):
    """Build the two tiers from .env using the Lab05 Tier class."""
    v = settings.values
    settings.tiers = tuple(
        Tier(
            name=name,
            role_name=v[f"{name.upper()}_ROLE_NAME"],
            profile_name=v[f"{name.upper()}_PROFILE_NAME"],
            lt_name=v[f"{name.upper()}_LT_NAME"],
            tg_name=v[f"{name.upper()}_TG_NAME"],
            asg_name=v[f"{name.upper()}_ASG_NAME"],
            listener_port=int(v[f"{name.upper()}_LISTENER_PORT"]),
            app_dir=settings.base_dir / f"{name}-app",
        )
        for name in ("weather", "advisor")
    )
    return settings


def secret_policy(parameter_arn):
    """Allow reading exactly one parameter."""
    return {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": parameter_arn,
        }],
    }


def ensure_role(aws, tier, parameter_arn):
    """Ensure a role/profile with access to only its own secret."""
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_role(
            RoleName=tier.role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy()),
        )
    aws.iam.put_role_policy(
        RoleName=tier.role_name,
        PolicyName=tier.policy_name,
        PolicyDocument=json.dumps(secret_policy(parameter_arn)),
    )
    with ignore_errors("EntityAlreadyExists"):
        aws.iam.create_instance_profile(
            InstanceProfileName=tier.profile_name
        )
    roles = aws.iam.get_instance_profile(
        InstanceProfileName=tier.profile_name
    )["InstanceProfile"]["Roles"]
    if not any(role["RoleName"] == tier.role_name for role in roles):
        aws.iam.add_role_to_instance_profile(
            InstanceProfileName=tier.profile_name,
            RoleName=tier.role_name,
        )


def runtime_environment(settings, tier, alb_dns, db_host):
    """Return non-secret service settings; secret values never enter here."""
    v = settings.values
    env = {
        "PORT": str(settings.app_port),
        "AWS_REGION": settings.region,
    }
    if tier.name == "weather":
        env.update({
            "NWS_USER_AGENT":
                f'(itmo-444-544 weather lab, {v["CONTACT_EMAIL"]})',
            "DB_HOST": db_host,
            "DB_PORT": v["DB_PORT"],
            "DB_NAME": v["DB_NAME"],
            "DB_USER": v["DB_USER"],
            "DB_PASSWORD_PARAM": v["DB_PASSWORD_PARAM"],
            "DB_CA_FILE": "/opt/app/rds-ca.pem",
            "CACHE_SECONDS": v["CACHE_SECONDS"],
        })
    else:
        env.update({
            "WEATHER_URL":
                f'http://{alb_dns}:{v["WEATHER_LISTENER_PORT"]}',
            "GEMINI_KEY_PARAM": v["GEMINI_PARAM_NAME"],
            "GEMINI_MODEL": v["GEMINI_MODEL"],
            "KEY_CACHE_SECONDS": v["KEY_CACHE_SECONDS"],
        })
    return env


def build_user_data(settings, tier, alb_dns, db_host):
    """Embed application code and non-secret configuration only."""
    app = base64.b64encode(
        (tier.app_dir / "app.js").read_bytes()
    ).decode()
    package = base64.b64encode(
        (tier.app_dir / "package.json").read_bytes()
    ).decode()
    env = runtime_environment(settings, tier, alb_dns, db_host)
    environment = "\n".join(
        "Environment=" + json.dumps(f"{key}={value}")
        for key, value in env.items()
    )
    setup = ""
    if tier.name == "weather":
        setup = (
            "dnf install -y postgresql15\n"
            "curl -fsS --retry 3 -o /opt/app/rds-ca.pem "
            "https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem\n"
        )
    script = f"""#!/bin/bash
set -euo pipefail
dnf install -y nodejs
mkdir -p /opt/app
{setup}
echo '{app}' | base64 -d > /opt/app/app.js
echo '{package}' | base64 -d > /opt/app/package.json
cd /opt/app
npm install --omit=dev
cat > /etc/systemd/system/{tier.name}.service << 'SERVICE'
[Unit]
Description={tier.name} application
After=network-online.target
Wants=network-online.target
[Service]
{environment}
WorkingDirectory=/opt/app
ExecStart=/usr/bin/node /opt/app/app.js
Restart=always
User=ec2-user
[Install]
WantedBy=multi-user.target
SERVICE
chown -R ec2-user:ec2-user /opt/app
systemctl daemon-reload
systemctl enable --now {tier.name}.service
"""
    if len(script.encode()) >= 16384:
        raise ValueError(f"{tier.name} user-data exceeds the 16 KB limit")
    return script


def template_data(settings, tier, alb_dns, db_host):
    """Build launch-template data without any secret values."""
    script = build_user_data(settings, tier, alb_dns, db_host)
    return {
        "ImageId": settings.values["AMI_ID"],
        "InstanceType": settings.values["INSTANCE_TYPE"],
        "KeyName": settings.values["KEY_NAME"],
        "SecurityGroupIds": [settings.app_sg_id],
        "IamInstanceProfile": {"Name": tier.profile_name},
        "UserData": base64.b64encode(script.encode()).decode(),
        "TagSpecifications": [{
            "ResourceType": "instance",
            "Tags": [{"Key": "Name", "Value": f"{tier.name}-app"}],
        }],
    }


def ensure_template(aws, settings, tier, alb_dns, db_host):
    """Create a template or a new version only when its data changed."""
    data = template_data(settings, tier, alb_dns, db_host)
    if not launch_template_exists(aws, tier):
        aws.ec2.create_launch_template(
            LaunchTemplateName=tier.lt_name,
            LaunchTemplateData=data,
        )
        return

    current = aws.ec2.describe_launch_template_versions(
        LaunchTemplateName=tier.lt_name,
        Versions=["$Latest"],
    )["LaunchTemplateVersions"][0]
    old = current["LaunchTemplateData"]
    if all(old.get(key) == value for key, value in data.items()):
        return
    aws.ec2.create_launch_template_version(
        LaunchTemplateName=tier.lt_name,
        SourceVersion=str(current["VersionNumber"]),
        LaunchTemplateData=data,
    )
