"""Orchestrate the Lab06 stack using the Lab05 infrastructure helpers."""

import time

from . import balancer, compute, network, scaling, storage
from .config import load_dotenv
from .util import retry_on
from .weather_compute import (
    build_user_data, configure_tiers, ensure_role, ensure_template,
)


def parameter_arn(settings, account, name):
    """Build a parameter ARN without reading the secret."""
    return f"arn:aws:ssm:{settings.region}:{account}:parameter{name}"


def check_prerequisites(settings, ssm):
    """Require DB outputs, Gemini metadata and valid user-data sizes."""
    output = settings.base_dir / "db_outputs.env"
    if not output.exists():
        raise ValueError("Database not ready: db_outputs.env is missing.")
    db = load_dotenv(output)
    if not db.get("DB_HOST"):
        raise ValueError("DB_HOST is missing from db_outputs.env.")
    name = settings.values["GEMINI_PARAM_NAME"]
    found = ssm.describe_parameters(
        ParameterFilters=[{
            "Key": "Name", "Option": "Equals", "Values": [name],
        }]
    )["Parameters"]
    if not found:
        raise ValueError("Gemini parameter is missing.")
    for tier in settings.tiers:
        build_user_data(settings, tier, "preflight-alb", db["DB_HOST"])
    return db


def create_weather_stack(settings, aws, ssm, account, sleep=time.sleep):
    """Create ALB before templates and ensure both application tiers."""
    configure_tiers(settings)
    v = settings.values
    db = check_prerequisites(settings, ssm)

    print("Ensuring roles and instance profiles...")
    for tier in settings.tiers:
        name = (
            v["DB_PASSWORD_PARAM"] if tier.name == "weather"
            else v["GEMINI_PARAM_NAME"]
        )
        ensure_role(aws, tier, parameter_arn(settings, account, name))

    print("Ensuring network and load balancer...")
    vpc = network.default_vpc_id(aws)
    subnets = [
        network.subnet_in_az(aws, vpc, settings.az_a),
        network.subnet_in_az(aws, vpc, settings.az_b),
    ]
    alb_sg = network.ensure_alb_security_group(aws, settings, vpc)
    network.restrict_app_security_group(aws, settings, alb_sg)
    groups = {
        tier.name: balancer.ensure_target_group(aws, settings, tier, vpc)
        for tier in settings.tiers
    }
    alb_arn, alb_dns = balancer.ensure_load_balancer(
        aws, settings, subnets, alb_sg
    )
    for tier in settings.tiers:
        balancer.ensure_listener(
            aws, alb_arn, tier.listener_port, groups[tier.name]
        )

    print("Ensuring launch templates; ALB DNS is now available...")
    for tier in settings.tiers:
        ensure_template(aws, settings, tier, alb_dns, db["DB_HOST"])

    print("Waiting briefly for IAM propagation...")
    sleep(15)
    print("Ensuring Auto Scaling Groups...")
    for tier in settings.tiers:
        retry_on(
            {"ValidationError", "InvalidParameterValue"},
            lambda tier=tier: scaling.ensure_asg(
                aws, tier, groups[tier.name], subnets
            ),
            attempts=6, delay=10, sleep=sleep,
        )

    outputs = {
        "VPC_ID": vpc,
        "SUBNET_A": subnets[0],
        "SUBNET_B": subnets[1],
        "ALB_SECURITY_GROUP_ID": alb_sg,
        "ALB_ARN": alb_arn,
        "ALB_DNS_NAME": alb_dns,
        "WEATHER_TG_ARN": groups["weather"],
        "ADVISOR_TG_ARN": groups["advisor"],
    }
    (settings.base_dir / "stack_outputs.env").write_text(
        "".join(f"{key}={value}\n" for key, value in outputs.items())
    )
    print("Stack created; first boot takes a few minutes.")
    print(f'Weather: http://{alb_dns}:{v["WEATHER_LISTENER_PORT"]}/weather?zip=60616')
    print(f'Advisor: http://{alb_dns}:{v["ADVISOR_LISTENER_PORT"]}/')
    return outputs


def delete_weather_stack(settings, aws, sleep=time.sleep):
    """Remove instances first, then ALB/network/templates/roles."""
    configure_tiers(settings)
    print("Deleting Auto Scaling Groups and their instances...")
    for tier in settings.tiers:
        scaling.delete_asg(aws, tier.asg_name, sleep=sleep)
        if scaling.find_asg(aws, tier.asg_name):
            raise RuntimeError("ASG still deleting; rerun cleanup shortly.")

    print("Deleting load balancer and target groups...")
    balancer.delete_load_balancer(aws, settings.alb_name)
    for tier in settings.tiers:
        retry_on(
            {"ResourceInUse"},
            lambda tier=tier: balancer.delete_target_group(aws, tier.tg_name),
            attempts=12, delay=10, sleep=sleep,
        )

    vpc = network.default_vpc_id(aws)
    group = network.find_security_group(aws, settings.alb_sg_name, vpc)
    network.restore_app_security_group(aws, settings, group)
    if group:
        network.delete_alb_security_group(aws, group, sleep=sleep)

    print("Deleting launch templates, profiles and roles...")
    for tier in settings.tiers:
        compute.delete_launch_template(aws, tier)
        storage.delete_role_and_profile(aws, tier)
    (settings.base_dir / "stack_outputs.env").unlink(missing_ok=True)
    print("Weather stack removed. Database and Gemini parameter remain.")
