# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""CodeDeploy AfterAllowTraffic hook for the cert-ra Litestar service.

Runs after CodeDeploy has shifted 100% of production traffic to green
(but before the deployment is marked Succeeded). A failure here triggers
CodeDeploy's automatic rollback to the previous task definition.

Checks the last `WINDOW_SECONDS` of CloudWatch metrics for the
production target group:

1. **5xx rate.** Sum of `HTTPCode_Target_5XX_Count` over the window.
   If non-zero (any 5xx during steady state after traffic shift), fail.
2. **p99 latency.** `TargetResponseTime` p99 over the window. If above
   `MAX_P99_LATENCY_MS`, fail.

Reports `Succeeded` / `Failed` to CodeDeploy via
`PutLifecycleEventHookExecutionStatus`. CodeDeploy's `auto_rollback`
config also watches its own alarms in parallel; the hook is a defence-
in-depth check that runs once at the end of the shift.
"""

from __future__ import annotations

import datetime
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

codedeploy = boto3.client("codedeploy")
cloudwatch = boto3.client("cloudwatch")

# Wait this long after the traffic shift completes before sampling
# metrics — gives CloudWatch's ingestion lag time to settle.
SETTLE_SECONDS = 60

# Default window over which we sum 5xx and compute p99. Operators
# can lengthen this in prod via env override.
DEFAULT_WINDOW_SECONDS = 120
DEFAULT_MAX_P99_LATENCY_MS = 2000


class HookFailure(RuntimeError):
    pass


def handler(event: dict, _context: object) -> None:
    deployment_id = event["DeploymentId"]
    hook_execution_id = event["LifecycleEventHookExecutionId"]
    logger.info(
        "AfterAllowTraffic hook starting for deployment=%s hook=%s",
        deployment_id,
        hook_execution_id,
    )

    target_group_arn = os.environ["PRODUCTION_TARGET_GROUP_ARN"]
    load_balancer_arn = os.environ["LOAD_BALANCER_ARN"]
    window_seconds = int(os.environ.get("WINDOW_SECONDS", DEFAULT_WINDOW_SECONDS))
    max_p99_ms = int(os.environ.get("MAX_P99_LATENCY_MS", DEFAULT_MAX_P99_LATENCY_MS))

    end = datetime.datetime.now(datetime.UTC)
    start = end - datetime.timedelta(seconds=window_seconds)

    try:
        _check_5xx_rate(target_group_arn, load_balancer_arn, start, end)
        _check_p99_latency(
            target_group_arn,
            load_balancer_arn,
            start,
            end,
            max_p99_ms=max_p99_ms,
        )
    except HookFailure as exc:
        logger.error("AfterAllowTraffic failed: %s", exc)
        _report(deployment_id, hook_execution_id, status="Failed")
        return
    except Exception:
        logger.exception("AfterAllowTraffic raised an unexpected error")
        _report(deployment_id, hook_execution_id, status="Failed")
        return

    logger.info("AfterAllowTraffic checks passed")
    _report(deployment_id, hook_execution_id, status="Succeeded")


def _alb_dimensions(
    *, target_group_arn: str, load_balancer_arn: str
) -> list[dict[str, str]]:
    """CloudWatch dimensions for ALB target group metrics.

    Both dimensions are required for the metric to resolve; AWS
    returns no datapoints when only TargetGroup is set.
    """
    # The TargetGroup dimension uses the suffix after `:targetgroup/`.
    # LoadBalancer uses the suffix after `:loadbalancer/`.
    tg_suffix = target_group_arn.split(":targetgroup/", 1)[-1]
    lb_suffix = load_balancer_arn.split(":loadbalancer/", 1)[-1]
    return [
        {"Name": "TargetGroup", "Value": "targetgroup/" + tg_suffix},
        {"Name": "LoadBalancer", "Value": lb_suffix},
    ]


def _check_5xx_rate(
    target_group_arn: str,
    load_balancer_arn: str,
    start: datetime.datetime,
    end: datetime.datetime,
) -> None:
    """Sum HTTPCode_Target_5XX_Count over the window. Any 5xx fails."""
    dims = _alb_dimensions(
        target_group_arn=target_group_arn, load_balancer_arn=load_balancer_arn
    )
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName="HTTPCode_Target_5XX_Count",
        Dimensions=dims,
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=["Sum"],
    )
    total_5xx = sum(point.get("Sum", 0) for point in response.get("Datapoints", []))
    logger.info("5xx count over window: %s", total_5xx)
    if total_5xx > 0:
        raise HookFailure(
            f"Saw {total_5xx} 5xx responses after traffic shift completed"
        )


def _check_p99_latency(
    target_group_arn: str,
    load_balancer_arn: str,
    start: datetime.datetime,
    end: datetime.datetime,
    *,
    max_p99_ms: int,
) -> None:
    """Get TargetResponseTime p99 over the window and compare against
    the configured ceiling. TargetResponseTime is in seconds."""
    dims = _alb_dimensions(
        target_group_arn=target_group_arn, load_balancer_arn=load_balancer_arn
    )
    response = cloudwatch.get_metric_statistics(
        Namespace="AWS/ApplicationELB",
        MetricName="TargetResponseTime",
        Dimensions=dims,
        StartTime=start,
        EndTime=end,
        Period=60,
        ExtendedStatistics=["p99"],
    )
    datapoints = response.get("Datapoints", [])
    if not datapoints:
        logger.warning(
            "No TargetResponseTime datapoints in window; skipping latency check"
        )
        return
    p99_seconds = max(
        point.get("ExtendedStatistics", {}).get("p99", 0.0) for point in datapoints
    )
    p99_ms = p99_seconds * 1000
    logger.info("p99 latency over window: %.2f ms (limit %d ms)", p99_ms, max_p99_ms)
    if p99_ms > max_p99_ms:
        raise HookFailure(
            f"p99 latency {p99_ms:.0f} ms exceeded ceiling {max_p99_ms} ms"
        )


def _report(deployment_id: str, hook_execution_id: str, *, status: str) -> None:
    codedeploy.put_lifecycle_event_hook_execution_status(
        deploymentId=deployment_id,
        lifecycleEventHookExecutionId=hook_execution_id,
        status=status,
    )
