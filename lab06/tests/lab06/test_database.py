"""Lab06 database tests against simulated AWS only."""

import copy

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from stack.clients import AwsClients
from stack.database import create_database, delete_database
from stack.weather_config import load_settings


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """Provide isolated AWS mocks and temporary output files."""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")

    settings = copy.copy(load_settings())
    settings.values = dict(settings.values)
    settings.base_dir = tmp_path

    with mock_aws():
        aws = AwsClients.create(settings.region)
        defaults = aws.ec2.describe_vpcs(
            Filters=[{"Name": "isDefault", "Values": ["true"]}]
        )["Vpcs"]
        vpc = (
            defaults[0]["VpcId"] if defaults
            else aws.ec2.create_default_vpc()["Vpc"]["VpcId"]
        )
        for az in (settings.az_a, settings.az_b):
            subnets = aws.ec2.describe_subnets(Filters=[
                {"Name": "vpc-id", "Values": [vpc]},
                {"Name": "availability-zone", "Values": [az]},
            ])["Subnets"]
            if not subnets:
                aws.ec2.create_default_subnet(AvailabilityZone=az)

        settings.app_sg_id = aws.ec2.create_security_group(
            GroupName="lab06-test-app",
            Description="Mock app group",
            VpcId=vpc,
        )["GroupId"]
        session = boto3.Session(region_name=settings.region)
        yield settings, aws, session.client("rds"), session.client("ssm")


def build(lab):
    """Create the mocked database without real waiting."""
    settings, aws, rds, ssm = lab
    create_database(settings, aws, rds, ssm, wait=False)
    return rds.describe_db_instances()["DBInstances"][0]


def test_database_settings(lab):
    """Verify the required private, encrypted, backup-free database."""
    db = build(lab)
    assert db["DBInstanceClass"] == "db.t4g.micro"
    assert db["AllocatedStorage"] == 20
    assert db["Engine"] == "postgres"
    assert db["BackupRetentionPeriod"] == 0
    assert db["MultiAZ"] is False
    assert db["PubliclyAccessible"] is False
    assert db["StorageEncrypted"] is True


def test_database_network_rule(lab):
    """Allow port 5432 from the app group and no CIDR ranges."""
    settings, aws, rds, ssm = lab
    db = build(lab)
    group = db["VpcSecurityGroups"][0]["VpcSecurityGroupId"]
    rules = aws.ec2.describe_security_groups(
        GroupIds=[group]
    )["SecurityGroups"][0]["IpPermissions"]
    assert len(rules) == 1
    rule = rules[0]
    assert rule["IpProtocol"] == "tcp"
    assert rule["FromPort"] == rule["ToPort"] == 5432
    assert rule["UserIdGroupPairs"][0]["GroupId"] == settings.app_sg_id
    assert not rule.get("IpRanges")
    assert not rule.get("Ipv6Ranges")


def test_create_is_idempotent(lab):
    """A second creation must not duplicate resources or rotate secrets."""
    settings, aws, rds, ssm = lab
    build(lab)
    version = ssm.get_parameter(
        Name=settings.values["DB_PASSWORD_PARAM"]
    )["Parameter"]["Version"]
    build(lab)
    assert len(rds.describe_db_instances()["DBInstances"]) == 1
    assert len(rds.describe_db_subnet_groups()["DBSubnetGroups"]) == 1
    assert ssm.get_parameter(
        Name=settings.values["DB_PASSWORD_PARAM"]
    )["Parameter"]["Version"] == version


def test_delete_leaves_no_database_snapshot_or_password(lab):
    """Cleanup removes resources and also tolerates a repeated deletion."""
    settings, aws, rds, ssm = lab
    build(lab)
    delete_database(settings, aws, rds, ssm, sleep=lambda _: None)
    delete_database(settings, aws, rds, ssm, sleep=lambda _: None)
    assert not rds.describe_db_instances()["DBInstances"]
    assert not rds.describe_db_snapshots()["DBSnapshots"]
    assert not rds.describe_db_subnet_groups()["DBSubnetGroups"]
    assert not (settings.base_dir / "db_outputs.env").exists()
    with pytest.raises(ClientError) as caught:
        ssm.get_parameter(Name=settings.values["DB_PASSWORD_PARAM"])
    assert caught.value.response["Error"]["Code"] == "ParameterNotFound"
