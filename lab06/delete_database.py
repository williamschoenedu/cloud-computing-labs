"""Delete the Lab06 database with no final snapshot or retained backups."""

import boto3

from stack.clients import AwsClients
from stack.database import delete_database
from stack.weather_config import load_settings


def main():
    """Load settings and completely remove the database."""
    settings = load_settings()
    session = boto3.Session(region_name=settings.region)
    delete_database(
        settings, AwsClients.create(settings.region),
        session.client("rds"), session.client("ssm"),
    )


if __name__ == "__main__":
    main()
