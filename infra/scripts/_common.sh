#!/usr/bin/env bash
# Shared helpers for cert-ra setup / upgrade scripts.
#
# Source this file at the top of `initial-setup.sh` / `upgrade.sh`:
#
#     SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#     source "$SCRIPT_DIR/_common.sh"
#
# Functions:
#   log_header           — print a section banner
#   log_step             — print a step banner
#   resolve_region       — deploy region: AWS_REGION override, else
#                          env_config.region from _config.py
#   resolve_domain       — env public domain from env_config.domain
#   await_and_print_zone_ns — poll Route53 + print the DnsStack zone's NS
#                          (background this during the DnsStack deploy)
#   run_temporal_schema_bootstrap — run the one-off Temporal RDS schema task
#                          (background this during the TemporalStack deploy)
#   require_sso_session  — verify the SSO session is valid + matches the
#                          expected permission set; trigger `aws sso login`
#                          if needed; hard-fail on a permission-set mismatch
#   stack_output         — read a single CFN output value by output key
#   run_migration_task   — invoke the cert-ra-migrate task on the
#                          MigrationsStack cluster and wait for exit 0
#
# Requires `aws`, `jq` on PATH. `ENV` env var must be set before any of the
# describe-stacks helpers are called.

# `set -e` is owned by the caller — sourcing this file should never abort
# the parent on its own.

log_header() {
    echo
    echo "==== $* ===="
    echo
}

log_step() {
    echo
    echo "---- $* ----"
}

# _env_config_field <env> <field>
#
# Echo a single field of EnvConfig (e.g. region, domain) for the given
# env, read straight from infra/cert_ra_infra/stacks/_config.py with
# stdlib python3 — no aws_cdk import, no `uv sync` needed — so it works
# at the very top of a script before the infra deps are installed. The
# config path is resolved relative to this file. Echoes nothing (and
# returns 0) if the field can't be read; callers supply the fallback.
_env_config_field() {
    local env="$1" field="$2"
    local common_dir config_py
    common_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    config_py="$common_dir/../cert_ra_infra/stacks/_config.py"
    # The module must be registered in sys.modules before exec_module:
    # _config.py uses @dataclass(slots=True) under `from __future__ import
    # annotations`, and dataclass's annotation resolution looks the module
    # up in sys.modules — it crashes if it isn't there.
    python3 -c '
import importlib.util, sys
spec = importlib.util.spec_from_file_location("cert_ra_env_config", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
sys.modules["cert_ra_env_config"] = mod
spec.loader.exec_module(mod)
print(getattr(mod.load_env(sys.argv[2]), sys.argv[3]))
' "$config_py" "$env" "$field" 2>/dev/null || true
}

# resolve_region <env>
#
# Single source of truth for the deploy region. Precedence:
#   1. AWS_REGION  — explicit operator override
#   2. env_config.region from _config.py (us-east-2 for every env)
#   3. us-east-2   — fallback if _config.py can't be read
resolve_region() {
    local env="${1:?resolve_region requires an env name}"
    if [[ -n "${AWS_REGION:-}" ]]; then
        echo "$AWS_REGION"
        return
    fi
    local region
    region="$(_env_config_field "$env" region)"
    echo "${region:-us-east-2}"
}

# resolve_domain <env>
#
# Public domain for the env (env_config.domain in _config.py), e.g.
# risk-staging.example.com. Used to look up the DnsStack hosted zone.
resolve_domain() {
    local env="${1:?resolve_domain requires an env name}"
    _env_config_field "$env" domain
}

# await_and_print_zone_ns <domain>
#
# Polls Route53 until a public hosted zone for <domain> exists, then
# prints its four authoritative NS records in a loud banner. Meant to be
# backgrounded alongside the DnsStack `cdk deploy`: that deploy blocks on
# ACM DNS validation, which can't succeed until the operator delegates
# these NS at Cloudflare — so they must be surfaced WHILE the deploy is
# still in progress (the DnsStack `NameServers` CFN output only prints
# after CREATE_COMPLETE, which is too late). Prints once and returns.
await_and_print_zone_ns() {
    local domain="${1:?await_and_print_zone_ns requires a domain}"
    local zid ns=""
    # ~10 min budget; the zone is created within the first minute of the
    # deploy, well before the cert's validation wait.
    local i
    for ((i = 0; i < 120; i++)); do
        zid=$(aws route53 list-hosted-zones-by-name --dns-name "$domain" \
            --query "HostedZones[?Name=='${domain}.'].Id | [0]" \
            --output text 2>/dev/null | sed 's|/hostedzone/||')
        if [[ -n "$zid" && "$zid" != "None" ]]; then
            ns=$(aws route53 get-hosted-zone --id "$zid" \
                --query 'DelegationSet.NameServers' --output text 2>/dev/null || true)
            [[ -n "$ns" ]] && break
        fi
        sleep 5
    done
    if [[ -z "$ns" ]]; then
        printf '\n!! Could not read NS for %s yet. Query it manually with:\n     aws route53 list-hosted-zones-by-name --dns-name %s\n' \
            "$domain" "$domain"
        return
    fi

    # Drop the bare NS list to a file first, so there's a reliable copy
    # even if the terminal output gets scrolled away or garbled.
    local ns_file="/tmp/cert-ra-${domain}-nameservers.txt"
    local ns_file_note=""
    if printf '%s\n' $ns >"$ns_file" 2>/dev/null; then
        ns_file_note="# (NS list also saved to ${ns_file})"$'\n'
    fi

    # Build the whole banner as one string and emit it with a single
    # printf — i.e. one write(2) — so the backgrounded poller's output
    # can't interleave with the foreground `cdk deploy` progress between
    # the NS lines and shred the banner (as it did before).
    local banner n
    banner=$'\n'
    banner+="########################################################################"$'\n'
    banner+="# ACTION REQUIRED — delegate NS at Cloudflare for ${domain}"$'\n'
    banner+="#"$'\n'
    banner+="# Add these four NS records for '${domain}' in the certora.com zone at"$'\n'
    banner+="# Cloudflare. The deploy is intentionally blocked on ACM DNS validation"$'\n'
    banner+="# and will auto-complete once delegation propagates. Do NOT cancel it."$'\n'
    banner+="#"$'\n'
    for n in $ns; do
        banner+="#     NS  ${domain}  ->  ${n}"$'\n'
    done
    banner+="########################################################################"$'\n'
    banner+="$ns_file_note"
    printf '%s' "$banner"
}

# run_temporal_schema_bootstrap <env>
#
# Runs the one-off Temporal RDS schema setup task. TemporalStack creates the
# schema-bootstrap task definition but does NOT run it, and the Temporal
# server services cannot start until the schema exists — so it must run
# out-of-band, concurrently with the TemporalStack deploy (background it).
# Waits for the cluster + a task def with a live task role to appear, runs the
# task in the private-egress subnets on cert-ra-temporal-fe-sg, and fails
# loudly on a non-zero exit. RDS enforces SSL (rds.force_ssl=1); the task def
# sets SQL_TLS so the connection succeeds.
run_temporal_schema_bootstrap() {
    local env="${1:?run_temporal_schema_bootstrap requires an env name}"
    local cluster="cert-ra-temporal-${env}"
    local family="cert-ra-temporal-schema-bootstrap"

    local vpc subnet_list sg
    vpc=$(stack_output "CertRa-NetworkStack-${env}" "VpcId")
    subnet_list=$(stack_output "CertRa-NetworkStack-${env}" "PrivateEgressSubnetIds" | tr -d ' ')
    sg=$(aws ec2 describe-security-groups \
        --filters "Name=group-name,Values=cert-ra-temporal-fe-sg" \
                  "Name=vpc-id,Values=$vpc" \
        --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)

    log_step "Temporal schema bootstrap: waiting for cluster + task def"
    local i clu rolearn
    for ((i = 0; i < 120; i++)); do  # up to ~20 min
        clu=$(aws ecs describe-clusters --clusters "$cluster" \
            --query 'clusters[0].status' --output text 2>/dev/null || echo NONE)
        rolearn=$(aws ecs describe-task-definition --task-definition "$family" \
            --query 'taskDefinition.taskRoleArn' --output text 2>/dev/null || echo "")
        if [[ "$clu" == ACTIVE && -n "$rolearn" && "$rolearn" != None ]] \
            && aws iam get-role --role-name "${rolearn##*/}" >/dev/null 2>&1; then
            break
        fi
        sleep 10
    done

    log_step "Running Temporal schema bootstrap on $cluster"
    local task_arn
    task_arn=$(aws ecs run-task --cluster "$cluster" --task-definition "$family" \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[${subnet_list}],securityGroups=[${sg}],assignPublicIp=DISABLED}" \
        --query 'tasks[0].taskArn' --output text 2>&1)
    if [[ "$task_arn" != arn:* ]]; then
        echo "!! Temporal schema bootstrap run-task failed: $task_arn" >&2
        return 1
    fi
    echo "  schema task: $task_arn"
    aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task_arn" 2>/dev/null || true
    local ec
    ec=$(aws ecs describe-tasks --cluster "$cluster" --tasks "$task_arn" \
        --query 'tasks[0].containers[0].exitCode' --output text 2>/dev/null)
    if [[ "$ec" == 0 ]]; then
        echo "  Temporal schema bootstrap complete (exit 0)"
    else
        echo "!! Temporal schema bootstrap FAILED (exit ${ec:-unknown}). The" >&2
        echo "!! Temporal services will not stabilise and the deploy will hang." >&2
        return 1
    fi
}

# require_sso_session <profile> <expected_permission_set>
#
# Confirms the AWS CLI has a valid SSO session for the given profile and
# that the assumed role's name contains the expected SSO permission set.
# Hard-fails if the role doesn't match — wrong role on a deploy script is
# a real operator mistake we should refuse, not work around.
require_sso_session() {
    local profile="$1"
    local expected_set="$2"

    if ! aws --profile "$profile" sts get-caller-identity >/dev/null 2>&1; then
        echo "SSO session for profile '$profile' is missing or expired."
        echo "Opening browser for sign-in..."
        aws sso login --profile "$profile"
    fi

    local arn
    arn=$(aws --profile "$profile" sts get-caller-identity --query Arn --output text)
    # AWSReservedSSO_<set>_<hash> is the standard IAM Identity Center
    # naming convention; matching the substring is enough to confirm
    # the permission set without parsing the role-name's random suffix.
    if [[ "$arn" != *"AWSReservedSSO_${expected_set}_"* ]]; then
        echo "Permission set mismatch."
        echo "  Expected: $expected_set"
        echo "  Got:      $arn"
        echo "Reconfigure profile or pick a different role."
        exit 1
    fi
}

# stack_output <stack_name> <output_key>
#
# Read one output value from a CloudFormation stack. Echoes the value
# to stdout. Exits 1 if the output doesn't exist.
stack_output() {
    local stack_name="$1"
    local output_key="$2"
    local value
    value=$(aws cloudformation describe-stacks \
        --stack-name "$stack_name" \
        --query "Stacks[0].Outputs[?OutputKey==\`${output_key}\`].OutputValue" \
        --output text)
    if [[ -z "$value" || "$value" == "None" ]]; then
        echo "Output '$output_key' not found on stack '$stack_name'" >&2
        exit 1
    fi
    echo "$value"
}

# run_migration_task
#
# Invokes the cert-ra-migrate task on the dedicated MigrationsStack
# cluster. Waits for the task to stop and fails the script if the
# container's exit code isn't 0. Reads the cluster + task definition
# family + migrate SG from MigrationsStack outputs; reads the subnets
# from NetworkStack outputs.
#
# Requires `ENV` to be set in the caller's environment.
run_migration_task() {
    : "${ENV:?ENV must be set (e.g. staging, prod)}"

    local cluster task_def sg subnets
    cluster=$(stack_output "CertRa-MigrationsStack-${ENV}" "ClusterName")
    task_def=$(stack_output "CertRa-MigrationsStack-${ENV}" "TaskDefinitionFamily")
    sg=$(stack_output "CertRa-MigrationsStack-${ENV}" "MigrateSecurityGroupId")
    subnets=$(stack_output "CertRa-NetworkStack-${ENV}" "PrivateEgressSubnetIds" \
        | tr ',' ' ')
    # Convert space-separated to comma-separated-without-spaces for the
    # awsvpcConfiguration subnet list format that `aws ecs run-task`
    # expects.
    local subnet_list
    subnet_list=$(echo "$subnets" | tr ' ' ',')

    log_step "Running migration task on $cluster"
    local task_arn
    task_arn=$(aws ecs run-task \
        --cluster "$cluster" \
        --task-definition "$task_def" \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[${subnet_list}],securityGroups=[${sg}],assignPublicIp=DISABLED}" \
        --query 'tasks[0].taskArn' --output text)
    echo "Migration task: $task_arn"

    aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task_arn"

    local exit_code
    exit_code=$(aws ecs describe-tasks \
        --cluster "$cluster" --tasks "$task_arn" \
        --query 'tasks[0].containers[0].exitCode' --output text)

    if [[ "$exit_code" != "0" ]]; then
        echo "Migration failed with exit code $exit_code"
        echo "Inspect logs: aws logs tail /ecs/cert-ra-migrate --since 5m"
        exit 1
    fi
    echo "Migration complete (exit 0)"
}

# run_seed_script <label> <command...>
#
# Invokes the cert-ra-migrate task definition with a container
# command override. The migrate image already has the full cert-ra
# Python venv + the `scripts/` directory baked in (`COPY .` in the
# Dockerfile), so we reuse it as the host for the manual-metrics
# seed scripts rather than provisioning a dedicated task family.
#
# Args:
#   $1  — short label for log lines (e.g. "governance")
#   $2+ — argv for the container command (e.g. python3 scripts/seed_x.py)
#
# Same VPC + SG as the migration task. Waits for completion and fails
# the calling script on a non-zero exit code.
run_seed_script() {
    : "${ENV:?ENV must be set (e.g. staging, prod)}"

    local label="$1"
    shift
    local cmd_args=("$@")

    local cluster task_def sg subnets subnet_list
    cluster=$(stack_output "CertRa-MigrationsStack-${ENV}" "ClusterName")
    task_def=$(stack_output "CertRa-MigrationsStack-${ENV}" "TaskDefinitionFamily")
    sg=$(stack_output "CertRa-MigrationsStack-${ENV}" "MigrateSecurityGroupId")
    subnets=$(stack_output "CertRa-NetworkStack-${ENV}" "PrivateEgressSubnetIds" \
        | tr ',' ' ')
    subnet_list=$(echo "$subnets" | tr ' ' ',')

    # Build the JSON for --overrides. The migrate task definition's
    # container name is "Migrate" (set in MigrationTask construct).
    local cmd_json
    cmd_json=$(python3 -c "
import json, sys
print(json.dumps({
    'containerOverrides': [{
        'name': 'Migrate',
        'command': sys.argv[1:],
    }]
}))" "${cmd_args[@]}")

    log_step "Running seed task: $label"
    local task_arn
    task_arn=$(aws ecs run-task \
        --cluster "$cluster" \
        --task-definition "$task_def" \
        --launch-type FARGATE \
        --network-configuration "awsvpcConfiguration={subnets=[${subnet_list}],securityGroups=[${sg}],assignPublicIp=DISABLED}" \
        --overrides "$cmd_json" \
        --query 'tasks[0].taskArn' --output text)
    echo "Seed task ($label): $task_arn"

    aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task_arn"

    local exit_code
    exit_code=$(aws ecs describe-tasks \
        --cluster "$cluster" --tasks "$task_arn" \
        --query 'tasks[0].containers[0].exitCode' --output text)

    if [[ "$exit_code" != "0" ]]; then
        echo "Seed task '$label' failed with exit code $exit_code"
        echo "Inspect logs: aws logs tail /ecs/cert-ra-migrate --since 5m"
        exit 1
    fi
    echo "Seed task '$label' complete (exit 0)"
}

# create_temporal_default_namespace
#
# Ensures the `default` Temporal namespace exists in the cluster.
#
# Why this is a separate helper:
# `temporalio/server:1.27.4` (the non-auto-setup image we use) does NOT
# auto-create the `default` namespace on first boot. Workers and the
# app reach the cluster fine over mTLS, but their first DescribeNamespace
# call fails with `NamespaceNotFound` and the container exits non-zero.
# Without this step the WorkersStack initial create silently rolls back
# because each worker task crash-loops on the missing namespace until
# ECS gives up. This was the unnamed failure mode that left prod
# without a workers stack for months.
#
# Implementation: invoke the maintenance container's task definition as
# a one-off via `aws ecs run-task` with a command override. The maint
# image bundles the `temporal` CLI + the mTLS wrapper, so all the SG
# and cert plumbing is already in place. Idempotent — describes first
# and only creates if missing.
#
# Requires `ENV` to be set and the MaintenanceStack to be deployed.
# Requires `TemporalStack` to have mTLS enforcement ON (the maint
# wrapper unconditionally injects --tls-* flags whenever the mTLS env
# vars are populated, so a plaintext frontend would refuse the
# handshake).
create_temporal_default_namespace() {
    : "${ENV:?ENV must be set (e.g. staging, prod)}"

    local cluster service
    cluster=$(stack_output "CertRa-MaintenanceStack-${ENV}" "ClusterName")
    service=$(stack_output "CertRa-MaintenanceStack-${ENV}" "ServiceName")

    # Reuse the running service's task def + network config so the
    # one-off task lands in the same subnets + SG as the long-lived
    # maint task. No need to reconstruct the JSON by hand.
    local svc_json task_def network_config
    svc_json=$(aws ecs describe-services \
        --cluster "$cluster" --services "$service" \
        --query 'services[0].{td:taskDefinition,nc:networkConfiguration}' \
        --output json)
    task_def=$(echo "$svc_json" | jq -r '.td')
    network_config=$(echo "$svc_json" | jq -c '.nc')

    # `bash -c "<describe> 2>/dev/null || <create>"`:
    #   - describe returns 0 if the namespace already exists → command
    #     short-circuits and the container exits 0
    #   - describe returns non-zero if missing → || runs the create,
    #     whose exit code becomes the final container exit code
    # The container has bash from the python:3.13-slim-bookworm base,
    # and `temporal` is the wrapped CLI on PATH.
    local cmd_json
    cmd_json=$(python3 -c "
import json
print(json.dumps({
    'containerOverrides': [{
        'name': 'Maint',
        'command': [
            'bash', '-c',
            'temporal operator namespace describe --namespace default '
            '>/dev/null 2>&1 && echo \"namespace default already exists\" '
            '|| temporal operator namespace create --namespace default '
            '--retention 720h',
        ],
    }]
}))")

    log_step "Ensuring Temporal default namespace exists"
    local task_arn
    task_arn=$(aws ecs run-task \
        --cluster "$cluster" \
        --task-definition "$task_def" \
        --launch-type FARGATE \
        --network-configuration "$network_config" \
        --overrides "$cmd_json" \
        --query 'tasks[0].taskArn' --output text)
    echo "Namespace bootstrap task: $task_arn"

    aws ecs wait tasks-stopped --cluster "$cluster" --tasks "$task_arn"

    local exit_code stopped_reason
    exit_code=$(aws ecs describe-tasks \
        --cluster "$cluster" --tasks "$task_arn" \
        --query 'tasks[0].containers[0].exitCode' --output text)
    stopped_reason=$(aws ecs describe-tasks \
        --cluster "$cluster" --tasks "$task_arn" \
        --query 'tasks[0].stoppedReason' --output text)

    if [[ "$exit_code" != "0" ]]; then
        echo "Namespace creation failed (exit=$exit_code reason=$stopped_reason)"
        echo "Inspect logs: aws logs tail /ecs/${service} --since 5m"
        exit 1
    fi
    echo "Default namespace ready (exit 0)"
}
