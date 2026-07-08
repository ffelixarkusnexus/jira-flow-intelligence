"""ECR repository for the backend container image.

Split from ComputeStack so the deploy workflow can `cdk deploy EcrStack` first,
push the image, then `cdk deploy ComputeStack` — solving the chicken-and-egg
where App Runner needs an image at create-time but the image needs ECR to
exist. Phase 2 retired the Connect-era frontend service (ADR-0019); only the
backend repo remains.

Tag mutability is MUTABLE so the workflow can push BOTH `:latest` (for App
Runner's image_identifier) AND `:$SHA` (for audit / rollback).
"""

from __future__ import annotations

from typing import Any

from aws_cdk import RemovalPolicy, Stack, Tags
from aws_cdk import aws_ecr as ecr
from constructs import Construct

from stacks._config import EnvConfig


class EcrStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        cfg: EnvConfig,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("env", cfg.name)
        Tags.of(self).add("app", "flow-intelligence")

        # RETAIN on stack delete so an accidental `cdk destroy` doesn't take
        # the image history with it. Manual deletion via console / CLI when
        # really intended.
        retention = RemovalPolicy.DESTROY if cfg.name == "dev" else RemovalPolicy.RETAIN

        self.backend_repo = ecr.Repository(
            self,
            "BackendRepo",
            repository_name=f"flow-intelligence-backend-{cfg.name}",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=retention,
        )

        # Connect-era frontend repo. Kept for ONE deploy after the
        # frontend-repo retirement so CFN can finish removing the
        # cross-stack import from ComputeStack
        # before deleting the export here. Drop in a follow-up PR — the
        # repo has already been emptied by hand.
        self.frontend_repo = ecr.Repository(
            self,
            "FrontendRepo",
            repository_name=f"flow-intelligence-frontend-{cfg.name}",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=retention,
        )
