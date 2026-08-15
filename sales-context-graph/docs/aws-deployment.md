# AWS deployment plan

This document describes how to deploy Sales Context Graph on AWS. It is a
deployment plan, not evidence that AWS infrastructure has already been
provisioned.

## Recommended architecture

```text
Internet
  -> Route 53 + ACM certificate
  -> Application Load Balancer
  -> ECS Fargate API (FastAPI)
  -> ECS Fargate worker (Redis ingestion queue)

API/worker -> Neo4j AuraDB in an AWS region
           -> managed Redis/Valkey
           -> Anthropic/OpenAI (or an explicitly implemented Bedrock adapter)
           -> CloudWatch and OpenTelemetry/Prometheus
```

ECS Fargate is preferred over Lambda because the application has a persistent
FastAPI process and a separate ingestion worker. The current Docker image and
separate worker command map directly to two ECS services.

## Required work

1. Push the image to Amazon ECR.
2. Create an ECS cluster and two task definitions/services: `api` and
   `worker`.
3. Expose only the API through an HTTPS Application Load Balancer. The worker
   must not be public.
4. Replace local Redis with managed ElastiCache Valkey/Redis. Configure it for
   the durability and replication required by the ingestion queue.
5. Keep Neo4j on AuraDB in the selected AWS region. AWS has no native managed
   Neo4j service; self-hosting Neo4j on EC2/EBS transfers backup, upgrade, HA,
   and restore responsibility to the team.
6. Store `NEO4J_*`, `REDIS_URL`, `WORKSPACE_API_KEYS`, LLM keys,
   `PANEL_TOKEN_SECRET`, and SSO settings in AWS Secrets Manager.
7. Configure `GET /health` and `GET /ready` as ECS/ALB health checks.
8. Set production flags explicitly:

   ```env
   ENV=production
   INGESTION_QUEUE_ENABLED=true
   DEMO_PUBLIC_ACCESS_ENABLED=false
   AUTHZ_ENFORCEMENT_ENABLED=true
   ```

   Enable SSO only after a real OIDC/JWKS issuer and claim mapping are
   configured.
9. Add Terraform/CDK and GitHub Actions using AWS OIDC for ECR/ECS deploys.
10. Add CloudWatch log retention, budget alarms, backups, and an incident/
    restore procedure before accepting production data.

## Cost ranges

These are planning ranges, not an AWS quote. Region, traffic, storage,
retention, HA, data transfer, and LLM usage can change the result.

| Profile | Approximate monthly AWS cost | Notes |
| --- | ---: | --- |
| Temporary demo | `$10–40` plus external Neo4j/LLM | One small API service; no HA; stop when unused |
| Controlled pilot | `$90–200` plus Neo4j and LLM | API + worker Fargate, ALB, managed Redis, logs, secrets |
| Minimal production | `$300–800+` plus Neo4j and LLM | Multiple API/worker tasks, Multi-AZ Redis, WAF/observability/backups |

Major cost drivers:

- Fargate/App Runner compute and memory;
- ElastiCache node/serverless capacity and cross-AZ traffic;
- ALB hourly and LCU charges;
- CloudWatch logs, metrics, retention, and dashboards;
- NAT Gateway and internet egress if private ECS tasks call AuraDB or an LLM;
- Neo4j AuraDB subscription;
- Anthropic/OpenAI token usage (or Bedrock usage after an adapter is added);
- backups, WAF, Route 53, ACM, Secrets Manager, and data transfer.

AWS pricing references:

- [AWS App Runner pricing](https://aws.amazon.com/apprunner/pricing/)
- [AWS ElastiCache pricing](https://aws.amazon.com/elasticache/pricing/)
- [AWS Elastic Load Balancing pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)
- [AWS Secrets Manager pricing](https://aws.amazon.com/secrets-manager/pricing/)
- [AWS CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)

## Suggested rollout

### Demo

Use one small API task, a separate worker only when queue-backed ingestion is
demonstrated, AuraDB Free/entry tier, minimal Redis, and CloudWatch logs. Keep
the demo key short-lived and disable public write operations.

### Pilot

Use ECS Fargate API and worker services, managed Redis/Valkey, HTTPS through an
ALB, Secrets Manager, budget alarms, and a real OIDC/JWKS provider. Keep the
worker private and set `INGESTION_QUEUE_ENABLED=true`.

### Production

Add multi-AZ services, Redis durability/replication, WAF, private networking,
centralized logs/SIEM export, managed Prometheus/Grafana or an equivalent,
backup/restore evidence, SLO/load evidence, tenant-fair scheduling, and a
tested disaster-recovery runbook.

## Current repository boundary

The existing Fly.io deployment recipe in
[`docs/deployment.md`](deployment.md) is the implemented deployment path.
AWS deployment requires new infrastructure configuration and credentials; it
does not require rewriting the core API or worker architecture.
