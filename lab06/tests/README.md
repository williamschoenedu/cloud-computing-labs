# Lab06 tests

## Moto tests

These tests use simulated AWS resources and run without real AWS credentials.

- [Database settings, networking, creation and deletion](lab06/test_database.py)
- [IAM policies, launch templates and stack creation](lab06/test_weather_stack.py)

[Recorded offline results](../evidence/offline-tests.txt)

## Application tests

These tests call the deployed application through the load balancer.

- [Weather, cache, history, ZIP errors and Advisor](lab06/test_application.py)

[Recorded application results](../evidence/application-tests.txt)

## Chaos tests

These tests temporarily interrupt the live application.

| Test | Source code | Recorded result |
|---|---|---|
| Database connection failure and restoration | [Database outage test](lab06/test_database_outage.py) | [Test log](../evidence/database-outage.txt) |
| Weather instance termination and replacement | [Instance recovery test](lab06/test_instance_recovery.py) | [Test log](../evidence/instance-recovery.txt) |
