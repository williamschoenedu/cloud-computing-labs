"""Lab06 configuration, using the Lab05 dotenv reader."""

from pathlib import Path
from types import SimpleNamespace

from .config import ConfigError, load_dotenv


def load_settings(path=".env"):
    """Load and validate the shared Lab06 configuration."""
    values = load_dotenv(path)
    required = [
        "AWS_REGION", "AMI_ID", "INSTANCE_TYPE", "KEY_NAME",
        "APP_SECURITY_GROUP_ID", "AZ_A", "AZ_B",
        "DB_INSTANCE_ID", "DB_INSTANCE_CLASS", "DB_NAME", "DB_USER",
        "DB_SG_NAME", "DB_SUBNET_GROUP_NAME", "DB_PASSWORD_PARAM",
        "DB_PORT", "APP_PORT", "CACHE_SECONDS",
        "ALB_NAME", "ALB_SG_NAME", "CONTACT_EMAIL",
        "GEMINI_PARAM_NAME", "GEMINI_MODEL", "KEY_CACHE_SECONDS",
    ]
    for tier in ("WEATHER", "ADVISOR"):
        required.extend(f"{tier}_{suffix}" for suffix in (
            "ROLE_NAME", "PROFILE_NAME", "LT_NAME",
            "TG_NAME", "ASG_NAME", "LISTENER_PORT",
        ))
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise ConfigError("Missing settings: " + ", ".join(missing))
    if values["AZ_A"] == values["AZ_B"]:
        raise ConfigError("AZ_A and AZ_B must differ.")
    return SimpleNamespace(
        values=values,
        base_dir=Path(path).resolve().parent,
        region=values["AWS_REGION"],
        az_a=values["AZ_A"],
        az_b=values["AZ_B"],
        app_sg_id=values["APP_SECURITY_GROUP_ID"],
        app_port=int(values["APP_PORT"]),
        alb_name=values["ALB_NAME"],
        alb_sg_name=values["ALB_SG_NAME"],
    )
