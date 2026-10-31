"""Create the Lab06 database using boto3."""

import boto3

from stack.clients import AwsClients
from stack.database import create_database
from stack.weather_config import load_settings


def main():
    """Load settings and create the database."""
    settings = load_settings()
    session = boto3.Session(region_name=settings.region)
    create_database(
        settings, AwsClients.create(settings.region),
        session.client("rds"), session.client("ssm"),
    )


if __name__ == "__main__":
    main()
