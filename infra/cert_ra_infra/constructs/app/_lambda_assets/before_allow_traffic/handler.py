# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Certora

"""CodeDeploy BeforeAllowTraffic hook for the cert-ra Litestar service.

Runs after CodeDeploy has registered the new task definition on the
green target group but before any production traffic is shifted to it.
A failure here aborts the deploy and CodeDeploy reverts atomically.

Two checks:

1. **Image signature presence (cosign).** Resolves the new task
   definition's container image to its ECR digest, then HEADs the
   sibling tag `sha256-<digest>.sig`. If the signature manifest is
   missing, the image was never signed by `gha-cert-ra-sign` and the
   deploy must not proceed. Full cryptographic verification against
   the cosign pubkey + KMS Verify lands in a follow-up — the
   presence check alone defeats the "push unsigned image to ECR and
   deploy" attack path because the sign step is the only way to
   create the `.sig` manifest.

2. **Green target group smoke test.** Probes the test listener (the
   internal-only :8443 listener that always points at green) on a
   small set of paths to confirm tasks are healthy and serving
   before traffic flips. Uses urllib to avoid bundling requests.

Reports `Succeeded` / `Failed` to CodeDeploy via
`PutLifecycleEventHookExecutionStatus`.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

codedeploy = boto3.client("codedeploy")
ecs = boto3.client("ecs")
ecr = boto3.client("ecr")
ssm = boto3.client("ssm")

# Smoke-test paths. /health is the load-bearing one; the others are
# light routes that exercise enough of the stack to catch most
# Container-up-but-misconfigured failures.
SMOKE_TEST_PATHS = ("/health", "/")

# Timeouts kept low — CodeDeploy waits for the hook synchronously and
# slow hooks make rollbacks themselves slow.
SMOKE_TEST_TIMEOUT_SECONDS = 5
SMOKE_TEST_RETRIES = 3


class HookFailure(RuntimeError):
    """Raised when a pre-shift check fails. The handler converts these
    into `Failed` reports to CodeDeploy."""


def handler(event: dict, _context: object) -> None:
    """CodeDeploy hook handler entry point."""
    deployment_id = event["DeploymentId"]
    hook_execution_id = event["LifecycleEventHookExecutionId"]
    logger.info(
        "BeforeAllowTraffic hook starting for deployment=%s hook=%s",
        deployment_id,
        hook_execution_id,
    )

    try:
        _verify_image_signature_presence(deployment_id)
        _smoke_test_green_target_group()
    except HookFailure as exc:
        logger.error("BeforeAllowTraffic failed: %s", exc)
        _report(deployment_id, hook_execution_id, status="Failed")
        return
    except Exception:
        logger.exception("BeforeAllowTraffic raised an unexpected error")
        _report(deployment_id, hook_execution_id, status="Failed")
        return

    logger.info("BeforeAllowTraffic checks passed")
    _report(deployment_id, hook_execution_id, status="Succeeded")


def _verify_image_signature_presence(deployment_id: str) -> None:
    """Resolve the new task def's image and HEAD its `.sig` manifest.

    Cosign stores signatures as separate OCI manifests tagged
    `sha256-<digest>.sig` in the same repo. ECR exposes this via
    `BatchGetImage` against the tag.
    """
    deployment = codedeploy.get_deployment(deploymentId=deployment_id)
    info = deployment["deploymentInfo"]
    revision = info.get("revision", {})

    task_def_arn = _extract_task_definition_arn(revision)
    if task_def_arn is None:
        # No task def in the AppSpec — unusual for an ECS deploy.
        # We don't want to silently skip the sig check, so fail closed.
        raise HookFailure(
            "Could not extract task definition ARN from CodeDeploy revision"
        )

    td = ecs.describe_task_definition(taskDefinition=task_def_arn)
    containers = td["taskDefinition"]["containerDefinitions"]
    app_container = next(
        (c for c in containers if c.get("name") == "App"),
        None,
    )
    if app_container is None:
        raise HookFailure("App container not found in task definition")

    image = app_container["image"]
    logger.info("Verifying signature presence for image=%s", image)

    repo, identifier = _split_image(image)
    digest = _resolve_to_digest(repo, identifier)

    # Cosign signature image tag: sha256-<hex>.sig (the colon in the
    # original digest becomes a hyphen).
    sig_tag = digest.replace(":", "-") + ".sig"
    try:
        ecr.describe_images(
            repositoryName=repo,
            imageIds=[{"imageTag": sig_tag}],
        )
    except ecr.exceptions.ImageNotFoundException as exc:
        raise HookFailure(
            f"No cosign signature found for {image} (digest={digest}, "
            f"expected ECR tag {sig_tag})"
        ) from exc

    logger.info("Cosign signature manifest present for digest=%s", digest)


def _smoke_test_green_target_group() -> None:
    """Hit the test listener on a couple of light paths; fail if any
    returns non-2xx after retries."""
    base_url = os.environ["TEST_LISTENER_URL"]
    # The ALB uses an ACM cert tied to the production hostname; the
    # hook reaches it via DNS (test listener is internal but reachable
    # from the Lambda's VPC). Cert validation is on because we want to
    # catch obvious misconfig, but we tolerate the SNI mismatch when
    # the operator points at an IP-based URL by allowing the optional
    # SKIP_TLS_VERIFY env override (used in `staging` only).
    skip_verify = (
        os.environ.get("SMOKE_TEST_SKIP_TLS_VERIFY", "false").lower() == "true"
    )
    ctx = ssl.create_default_context()
    if skip_verify:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        logger.warning("SMOKE_TEST_SKIP_TLS_VERIFY=true; skipping cert validation")

    for path in SMOKE_TEST_PATHS:
        url = base_url.rstrip("/") + path
        _probe_with_retries(url, ctx=ctx)
    logger.info("Smoke test passed for %d paths", len(SMOKE_TEST_PATHS))


def _probe_with_retries(url: str, *, ctx: ssl.SSLContext) -> None:
    last_error: Exception | None = None
    for attempt in range(1, SMOKE_TEST_RETRIES + 1):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(
                req,
                timeout=SMOKE_TEST_TIMEOUT_SECONDS,
                context=ctx,
            ) as response:
                if 200 <= response.status < 300:
                    logger.info("OK %s (%d) attempt=%d", url, response.status, attempt)
                    return
                last_error = HookFailure(
                    f"Probe to {url} returned status={response.status}"
                )
        except urllib.error.HTTPError as exc:
            last_error = HookFailure(
                f"Probe to {url} returned HTTP error {exc.code}: {exc.reason}"
            )
        except urllib.error.URLError as exc:
            last_error = HookFailure(f"Probe to {url} failed: {exc.reason}")
        except Exception as exc:
            last_error = HookFailure(
                f"Probe to {url} raised {type(exc).__name__}: {exc}"
            )

    assert last_error is not None  # at least one attempt must have run
    raise last_error


def _extract_task_definition_arn(revision: dict) -> str | None:
    """CodeDeploy ECS revisions carry the AppSpec under `string.content`
    as a YAML/JSON blob with a TaskDefinition arn."""
    rev_type = revision.get("revisionType")
    if rev_type != "AppSpecContent":
        return None
    content = revision.get("appSpecContent", {}).get("content")
    if not content:
        return None
    try:
        spec = json.loads(content)
    except json.JSONDecodeError:
        # AppSpec can also be YAML; we don't bundle a YAML parser to
        # keep the Lambda lean. Fall back to a substring search.
        return _grep_taskdef_arn(content)
    resources = spec.get("Resources", [])
    for entry in resources:
        for value in entry.values():
            props = value.get("Properties", {})
            arn = props.get("TaskDefinition")
            if isinstance(arn, str):
                return arn
    return None


def _grep_taskdef_arn(content: str) -> str | None:
    """Tiny YAML-tolerant fallback: scan for arn:aws:ecs:...:task-definition/..."""
    marker = "arn:aws:ecs:"
    if marker not in content:
        return None
    start = content.index(marker)
    end = start
    while end < len(content) and content[end] not in {'"', "'", " ", "\n", "\r"}:
        end += 1
    return content[start:end]


def _split_image(image: str) -> tuple[str, str]:
    """Split `<registry>/<repo>:<tag>` or `<registry>/<repo>@<digest>`
    into (repo_name, identifier_after_at_or_colon).
    """
    # ECR image refs look like:
    #   123456789012.dkr.ecr.us-east-1.amazonaws.com/cert-ra:sha-abc
    # We just need the repo name (cert-ra) and either tag or digest.
    if "@" in image:
        ref, identifier = image.rsplit("@", 1)
    elif ":" in image:
        ref, identifier = image.rsplit(":", 1)
    else:
        raise HookFailure(f"Image reference has no tag or digest: {image}")
    repo = ref.rsplit("/", 1)[-1]
    return repo, identifier


def _resolve_to_digest(repo: str, identifier: str) -> str:
    """Resolve a tag or digest to the canonical sha256:... digest."""
    if identifier.startswith("sha256:"):
        return identifier
    response = ecr.describe_images(
        repositoryName=repo,
        imageIds=[{"imageTag": identifier}],
    )
    images = response.get("imageDetails", [])
    if not images:
        raise HookFailure(f"Tag {identifier} not found in repo {repo}")
    return images[0]["imageDigest"]


def _report(deployment_id: str, hook_execution_id: str, *, status: str) -> None:
    codedeploy.put_lifecycle_event_hook_execution_status(
        deploymentId=deployment_id,
        lifecycleEventHookExecutionId=hook_execution_id,
        status=status,
    )
