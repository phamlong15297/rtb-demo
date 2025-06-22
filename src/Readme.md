# Realtime backend - Pastebin MVP

## How to develop in local
```
cd src
docker compose up --build
```
## How to deploy
AWS services will be used:
* EC2 Instance (for running nginx, FastAPI)
* Amazon RDS (MySQL)
* Amazon ElastiCache (Redis)
* Amazon S3 (instead of MinIO)

### Setup

### Compromise
* Use 1 Ec2 instance for demo
* No database replication (master-slave) 
    * DB is a single point of failure

### Public url
The website is deployed at `3.217.244.189`
