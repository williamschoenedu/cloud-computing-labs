"""Test Lab06 infrastructure without real AWS calls."""

import base64
import shutil
from pathlib import Path

import pytest

from tests.lab06.test_database import lab
from stack import balancer
from stack.weather_compute import build_user_data, configure_tiers
from stack.weather_stack import create_weather_stack, parameter_arn


@pytest.fixture
def prepared(lab):
    """Provide mock networking, application files and fake secrets."""
    settings, aws, rds, ssm = lab
    for name in ("weather-app", "advisor-app"):
        shutil.copytree(
            Path.cwd() / name, settings.base_dir / name,
            ignore=shutil.ignore_patterns("node_modules"),
        )
    settings.values["AMI_ID"] = aws.ec2.register_image(
        Name="lab06-mock-image",
        Architecture="x86_64",
        RootDeviceName="/dev/xvda",
        VirtualizationType="hvm",
    )["ImageId"]
    aws.ec2.create_key_pair(KeyName=settings.values["KEY_NAME"])
    (settings.base_dir / "db_outputs.env").write_text(
        "DB_HOST=database.mock.internal\n"
        "DB_SECURITY_GROUP_ID=sg-mock\n"
    )
    for name in ("GEMINI_PARAM_NAME", "DB_PASSWORD_PARAM"):
        ssm.put_parameter(
            Name=settings.values[name],
            Value="MOCK_SECRET_NOT_REAL",
            Type="SecureString",
        )
    configure_tiers(settings)
    return settings, aws, ssm


def build(prepared):
    """Create the simulated stack with propagation waits disabled."""
    settings, aws, ssm = prepared
    return create_weather_stack(
        settings, aws, ssm, "123456789012", sleep=lambda _: None
    )


def test_roles_read_only_their_own_secret(prepared):
    """Both generated IAM policies grant only one specific secret."""
    settings, aws, ssm = prepared
    build(prepared)
    for tier in settings.tiers:
        policy = aws.iam.get_role_policy(
            RoleName=tier.role_name, PolicyName=tier.policy_name
        )["PolicyDocument"]
        key = (
            "DB_PASSWORD_PARAM" if tier.name == "weather"
            else "GEMINI_PARAM_NAME"
        )
        assert policy["Statement"] == [{
            "Effect": "Allow",
            "Action": ["ssm:GetParameter"],
            "Resource": parameter_arn(
                settings, "123456789012", settings.values[key]
            ),
        }]


def test_alb_exists_before_templates(prepared, monkeypatch):
    """Template creation sees an existing ALB and its actual DNS."""
    settings, aws, ssm = prepared
    import stack.weather_stack as module
    original = module.ensure_template
    seen = []

    def checked(aws, settings, tier, alb_dns, db_host):
        alb = balancer.find_load_balancer(aws, settings.alb_name)
        assert alb is not None
        assert alb["DNSName"] == alb_dns
        seen.append(tier.name)
        return original(aws, settings, tier, alb_dns, db_host)

    monkeypatch.setattr(module, "ensure_template", checked)
    outputs = build(prepared)
    assert seen == ["weather", "advisor"]
    for tier in settings.tiers:
        template = aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=tier.lt_name, Versions=["$Latest"]
        )["LaunchTemplateVersions"][0]["LaunchTemplateData"]
        text = base64.b64decode(template["UserData"]).decode()
        assert "MOCK_SECRET_NOT_REAL" not in text
        if tier.name == "advisor":
            assert outputs["ALB_DNS_NAME"] in text
        else:
            assert "database.mock.internal" in text
            assert "Environment=\"DB_PASSWORD=" not in text


@pytest.mark.parametrize("name", ["weather", "advisor"])
def test_user_data_below_16_kb(prepared, name):
    """Each tier stays below EC2's decoded user-data size limit."""
    settings, aws, ssm = prepared
    tier = next(t for t in settings.tiers if t.name == name)
    text = build_user_data(
        settings, tier, "test-alb.example.com", "database.mock.internal"
    )
    assert len(text.encode()) < 16384


def test_stack_creation_is_idempotent(prepared):
    """Repeated creation adds no roles, templates, ASGs or ALBs."""
    settings, aws, ssm = prepared
    build(prepared)
    build(prepared)
    assert len(aws.elbv2.describe_load_balancers()["LoadBalancers"]) == 1
    assert len(aws.elbv2.describe_target_groups()["TargetGroups"]) == 2
    assert len(
        aws.autoscaling.describe_auto_scaling_groups()["AutoScalingGroups"]
    ) == 2
    assert len(aws.iam.list_roles()["Roles"]) == 2
    assert len(
        aws.iam.list_instance_profiles()["InstanceProfiles"]
    ) == 2
    assert len(aws.ec2.describe_launch_templates()["LaunchTemplates"]) == 2
    for tier in settings.tiers:
        versions = aws.ec2.describe_launch_template_versions(
            LaunchTemplateName=tier.lt_name
        )["LaunchTemplateVersions"]
        assert len(versions) == 1
