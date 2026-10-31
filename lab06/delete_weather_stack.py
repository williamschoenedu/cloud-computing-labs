"""Remove the Weather Advisor stack, leaving DB cleanup separate."""

import logging
import sys

from botocore.exceptions import BotoCoreError, ClientError

from stack.clients import AwsClients
from stack.weather_config import load_settings
from stack.weather_stack import delete_weather_stack


def main():
    """Remove stack resources using the shared configuration."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings()
    delete_weather_stack(settings, AwsClients.create(settings.region))


if __name__ == "__main__":
    try:
        main()
    except ClientError as error:
        print("AWS error:", error.response["Error"]["Code"])
        sys.exit(1)
    except (ValueError, RuntimeError, BotoCoreError) as error:
        print("Cleanup stopped:", str(error))
        sys.exit(1)
