"""CDK stacks for Flow Intelligence on AWS.

Per ADR-0012 + ADR-0013: AWS App Runner + RDS Postgres + Secrets Manager,
deployed via CDK (Python) and GitHub Actions OIDC. Four small stacks per
concern so any one can be redeployed without churning the others:

- network_stack    — VPC, security groups (Phase 1 keeps RDS in a public
                     subnet with strict SG + IAM auth to skip NAT GW cost;
                     Phase 2 moves it private)
- data_stack       — RDS Postgres db.t4g.micro Single-AZ + Secrets Manager
- compute_stack    — ECR repos + App Runner services for backend + frontend
- observability    — CloudWatch alarms + billing alarm at $100
"""
