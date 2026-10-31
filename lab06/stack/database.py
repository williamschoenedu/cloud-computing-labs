"""Create and delete the private Lab06 PostgreSQL database."""

import secrets
import time

from botocore.exceptions import ClientError

from . import network
from .config import load_dotenv
from .util import error_code, ignore_errors, retry_on


def find_database(rds, identifier):
    """Return the database, or None only when it does not exist."""
    try:
        return rds.describe_db_instances(
            DBInstanceIdentifier=identifier
        )["DBInstances"][0]
    except ClientError as error:
        if error_code(error) != "DBInstanceNotFound":
            raise
        return None


def database_rule(settings):
    """Allow PostgreSQL only from the shared app security group."""
    port = int(settings.values["DB_PORT"])
    return [{
        "IpProtocol": "tcp",
        "FromPort": port,
        "ToPort": port,
        "UserIdGroupPairs": [{"GroupId": settings.app_sg_id}],
    }]


def ensure_database_network(settings, aws, rds):
    """Reuse Lab05 VPC helpers and ensure DB networking exists."""
    v = settings.values
    vpc = network.default_vpc_id(aws)
    subnets = [
        network.subnet_in_az(aws, vpc, settings.az_a),
        network.subnet_in_az(aws, vpc, settings.az_b),
    ]
    group = network.find_security_group(aws, v["DB_SG_NAME"], vpc)
    if group is None:
        group = aws.ec2.create_security_group(
            GroupName=v["DB_SG_NAME"],
            Description="PostgreSQL from lab app security group only",
            VpcId=vpc,
        )["GroupId"]
    with ignore_errors("InvalidPermission.Duplicate"):
        aws.ec2.authorize_security_group_ingress(
            GroupId=group, IpPermissions=database_rule(settings)
        )
    try:
        rds.describe_db_subnet_groups(
            DBSubnetGroupName=v["DB_SUBNET_GROUP_NAME"]
        )
    except ClientError as error:
        if error_code(error) != "DBSubnetGroupNotFoundFault":
            raise
        rds.create_db_subnet_group(
            DBSubnetGroupName=v["DB_SUBNET_GROUP_NAME"],
            DBSubnetGroupDescription="Lab06 database subnets",
            SubnetIds=subnets,
        )
    return group


def ensure_password(ssm, name):
    """Keep a previous password after a partial run; otherwise generate one."""
    try:
        return ssm.get_parameter(
            Name=name, WithDecryption=True
        )["Parameter"]["Value"]
    except ClientError as error:
        if error_code(error) != "ParameterNotFound":
            raise
    password = secrets.token_hex(16)
    ssm.put_parameter(
        Name=name, Value=password,
        Type="SecureString", Tier="Standard", Overwrite=False,
    )
    return password


def database_request(settings, group, password):
    """Build the RDS request; never print or persist this dictionary."""
    v = settings.values
    return {
        "DBInstanceIdentifier": v["DB_INSTANCE_ID"],
        "DBInstanceClass": v["DB_INSTANCE_CLASS"],
        "Engine": "postgres",
        "AllocatedStorage": 20,
        "StorageType": "gp2",
        "StorageEncrypted": True,
        "MasterUsername": v["DB_USER"],
        "MasterUserPassword": password,
        "DBName": v["DB_NAME"],
        "DBSubnetGroupName": v["DB_SUBNET_GROUP_NAME"],
        "VpcSecurityGroupIds": [group],
        "AvailabilityZone": settings.az_a,
        "Port": int(v["DB_PORT"]),
        "BackupRetentionPeriod": 0,
        "MultiAZ": False,
        "PubliclyAccessible": False,
        "AutoMinorVersionUpgrade": False,
        "DeletionProtection": False,
    }


def create_database(settings, aws, rds, ssm, wait=True):
    """Idempotently create the DB and save only non-secret outputs."""
    v = settings.values
    group = ensure_database_network(settings, aws, rds)
    db = find_database(rds, v["DB_INSTANCE_ID"])
    if db is None:
        password = ensure_password(ssm, v["DB_PASSWORD_PARAM"])
        try:
            rds.create_db_instance(
                **database_request(settings, group, password)
            )
        finally:
            password = None
        print("Database creation requested.")
    else:
        print("Database already exists; keeping it.")

    if wait:
        print("Waiting for PostgreSQL; usually several minutes...")
        rds.get_waiter("db_instance_available").wait(
            DBInstanceIdentifier=v["DB_INSTANCE_ID"],
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )
    db = find_database(rds, v["DB_INSTANCE_ID"])
    if db and db.get("Endpoint"):
        host = db["Endpoint"]["Address"]
        (settings.base_dir / "db_outputs.env").write_text(
            f"DB_HOST={host}\nDB_SECURITY_GROUP_ID={group}\n"
        )
        print("Database endpoint:", host)
    return db


def delete_database(settings, aws, rds, ssm, sleep=time.sleep):
    """Delete DB without snapshots/backups, then its dependencies."""
    v = settings.values
    identifier = v["DB_INSTANCE_ID"]
    db = find_database(rds, identifier)
    if db:
        if db["DBInstanceStatus"] != "deleting":
            rds.delete_db_instance(
                DBInstanceIdentifier=identifier,
                SkipFinalSnapshot=True,
                DeleteAutomatedBackups=True,
            )
        print("Waiting for database deletion...")
        rds.get_waiter("db_instance_deleted").wait(
            DBInstanceIdentifier=identifier,
            WaiterConfig={"Delay": 30, "MaxAttempts": 60},
        )
    with ignore_errors("DBSubnetGroupNotFoundFault"):
        rds.delete_db_subnet_group(
            DBSubnetGroupName=v["DB_SUBNET_GROUP_NAME"]
        )

    vpc = network.default_vpc_id(aws)
    group = network.find_security_group(aws, v["DB_SG_NAME"], vpc)
    if group:
        with ignore_errors("InvalidGroup.NotFound"):
            retry_on(
                {"DependencyViolation"},
                lambda: aws.ec2.delete_security_group(GroupId=group),
                attempts=20, delay=15, sleep=sleep,
            )
    with ignore_errors("ParameterNotFound"):
        ssm.delete_parameter(Name=v["DB_PASSWORD_PARAM"])
    (settings.base_dir / "db_outputs.env").unlink(missing_ok=True)
    print("Database, subnet group, security group and password removed.")
