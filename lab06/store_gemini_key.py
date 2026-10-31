"""Store the Gemini key in SSM without writing it to a local file."""

import getpass
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def main():
    """Read configuration and store the secretly entered key."""
    settings = {}
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            name, value = line.split("=", 1)
            settings[name.strip()] = value.strip()

    ssm = boto3.client("ssm", region_name=settings["AWS_REGION"])
    key = getpass.getpass("Gemini API key (verdeckt): ").strip()
    if not key:
        print("Keine Eingabe; nichts gespeichert.")
        return

    try:
        ssm.put_parameter(
            Name=settings["GEMINI_PARAM_NAME"],
            Value=key,
            Type="SecureString",
            Tier="Standard",
            Overwrite=True,
        )
        print("Gespeichert:", settings["GEMINI_PARAM_NAME"])
        print("Typ: SecureString | Tier: Standard")
    except ClientError as error:
        print("AWS-Fehler:", error.response["Error"]["Code"])
    except BotoCoreError:
        print("AWS-Verbindungs- oder Konfigurationsfehler.")
    finally:
        key = None


if __name__ == "__main__":
    main()
