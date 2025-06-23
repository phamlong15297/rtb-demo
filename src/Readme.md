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

When deploying, we remove `db, redis, and MinIO` from `compose.yml`, then create `AWS RDS, ElastiCache, and S3`, and connect them to the EC2 instance.

For the demo, I am using an EC2 t2.micro instance (1 GB RAM) and the smallest available size of AWS RDS and ElastiCache. Since this application is lightweight, these resources are sufficient to run the demo smoothly.

### Compromise
* Use 1 Ec2 instance for demo
* No database replication (master-slave) 
    * DB is a single point of failure

### Public url
The website is deployed at `3.217.244.189`
