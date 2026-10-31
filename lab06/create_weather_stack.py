"""Create the two-tier Weather Advisor infrastructure."""

import logging
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from stack.clients import AwsClients
from stack.weather_config import load_settings
from stack.weather_stack import create_weather_stack


def main():
    """Load config and orchestrate the stack."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    session = boto3.Session(region_name=settings.region)
    account = session.client("sts").get_caller_identity()["Account"]
    create_weather_stack(
        settings, AwsClients.create(settings.region),
        session.client("ssm"), account,
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        print("AWS error:", error.response["Error"]["Code"])
        sys.exit(1)
    except (ValueError, RuntimeError, BotoCoreError) as error:
        print("Stack creation stopped:", str(error))
        sys.exit(1)
