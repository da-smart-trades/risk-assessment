# Secrets Rotation

How to rotate each cert-ra secret without downtime. **Five secrets**
plus the Temporal mTLS cert family (see [temporal-mtls-rotation.md](temporal-mtls-rotation.md)
for the cert path).

All five live under `/cert-ra/${env}/…` and are encrypted with the
`cert-ra-secrets-cmk` KMS CMK. Two of them are MFA-gated for writes:
`oauth/providers` and `app/session-secret`.

| Secret | Cadence | MFA write | Owner | Downtime risk |
| --- | --- | --- | --- | --- |
| `/cert-ra/${env}/oauth/providers` | Annual / on compromise | ✅ | Auth team | Rewriting hijacks the IdP round-trip until provider config updates. |
| `/cert-ra/${env}/rpc/providers` | Annual / on compromise | ❌ | Data team | Rewriting causes worker fetch failures until tasks restart. |
| `/cert-ra/${env}/app/session-secret` | Annual / on compromise | ✅ | Auth team | Rewriting invalidates every active session. |
| `/cert-ra/${env}/email/resend-api-key` | Annual / on compromise | ❌ | Comms | Rewriting blocks transactional email until tasks restart. |
| `/cert-ra/${env}/sentry/dsn` | On suspected compromise only | ❌ | Observability | Rewriting drops in-flight error reports during the cutover. |

ECS tasks pick up the new secret value at next **task restart**.
Steady-state deploys naturally cycle tasks well within any sane
rotation window.

---

## Generic rotation procedure

For any secret in the table above:

1. **Stage the new value out of band.** Generate the new key /
   client-secret / DSN. Keep a copy somewhere durable (1Password vault,
   etc.).
2. **Write the new value to Secrets Manager.**
   ```bash
   aws --profile cert-ra-${env}-upgrader secretsmanager put-secret-value \
       --secret-id "/cert-ra/${env}/email/resend-api-key" \
       --secret-string 're_xxxxxxxxxxxxxxxxxxxxxxxx'
   ```
   For the two MFA-gated secrets, the request must include an MFA
   session token: `aws sts get-session-token --serial-number
   arn:aws:iam::ACCOUNT:mfa/USER --token-code 123456`.

   For the JSON-blob secrets (`oauth/providers`, `rpc/providers`), pass
   a full JSON payload — partial updates are not supported by Secrets
   Manager.
3. **Cycle the consumers.** Either wait for the next routine deploy
   (which rotates every ECS task) or force a rolling restart:
   ```bash
   # App
   aws ecs update-service \
       --cluster cert-ra-app-${env}-cluster \
       --service cert-ra-app-${env} \
       --force-new-deployment

   # Workers
   aws ecs update-service \
       --cluster cert-ra-workers-${env} \
       --service cert-ra-worker-metrics-${env} \
       --force-new-deployment
   aws ecs update-service \
       --cluster cert-ra-workers-${env} \
       --service cert-ra-worker-alerts-${env} \
       --force-new-deployment
   ```
4. **Verify** the new value is in use by checking the consumer's logs
   for the rotation marker (or, for OAuth, by completing an
   end-to-end sign-in).
5. **Revoke the old value** at the upstream provider once you've
   confirmed all consumers are on the new value.

---

## Per-secret notes

### `oauth/providers`

JSON blob with three nested objects (`google`, `github`, `microsoft`),
each carrying `client_id` + `client_secret`.

- **Rotation cadence**: annual or on any suspected leak.
- **MFA-gated** — Upgrader cannot rewrite without an MFA token.
- **Downtime risk**: rewriting before updating the provider-side
  redirect URI / client secret rolls every in-flight OAuth round-trip
  to failure. Update the provider side **first**, then write the new
  Secrets Manager value, then force-restart the app.
- **Verification**: complete a sign-in via each provider after restart.

### `rpc/providers`

JSON blob keyed by chain (`eth`, `base`, `arbitrum`, `solana`). Each
value is an opaque API key / URL.

- **Rotation cadence**: annual or on suspected leak.
- **No MFA gate** — provider keys rotate often enough that requiring
  MFA per rotation creates more friction than security gain.
- **Downtime risk**: workers fail RPC fetches with the old key from
  the moment the upstream provider revokes it until tasks restart.
  Schedule rotations during off-peak (workers re-poll on next task
  start; in-flight tasks see retries).
- **Verification**: tail worker logs for successful RPC fetches:
  ```bash
  aws logs tail /ecs/cert-ra-worker-metrics-${env} --since 5m \
      | grep -i 'rpc'
  ```

### `app/session-secret`

Opaque 32+ byte secret used by Litestar to sign session cookies.

- **Rotation cadence**: annual, or immediately on any session-token
  leak / Slack-bot accident.
- **MFA-gated** — Upgrader cannot rewrite without MFA.
- **Downtime risk**: rewriting **logs out every active user**. Old
  cookies signed with the previous secret no longer validate.
- **Recommended pattern**: announce the cutover in advance, rotate
  during a low-traffic window, then force-restart the app.
- **Verification**: sign in fresh; confirm the cookie's signature
  matches the new secret.

### `email/resend-api-key`

Opaque API key for Resend (transactional email).

- **Rotation cadence**: annual.
- **Downtime risk**: outbound email blocked from the moment Resend
  revokes the old key until tasks restart. In-flight `send_email`
  retries fail.
- **Verification**: trigger a password-reset email + confirm delivery.

### `sentry/dsn`

Full Sentry DSN URL.

- **Rotation cadence**: only on suspected compromise (e.g. accidental
  commit). Sentry treats DSN exposure as low risk because the DSN
  only grants write to a single project's ingestion endpoint.
- **Downtime risk**: in-flight error reports during the cutover are
  dropped on the floor. Replace the DSN at the Sentry side, write the
  new value, restart consumers.

---

## Auditing rotations

Every `secretsmanager:PutSecretValue` call lands in CloudTrail. The
`cert-ra-secrets-cmk` KMS key policy also logs `kms:Decrypt` events
for every read by an ECS task.

```bash
# Recent secret rotations
aws cloudtrail lookup-events \
    --lookup-attributes AttributeKey=EventName,AttributeValue=PutSecretValue \
    --start-time "$(date -u -d '7 days ago' --iso-8601=seconds)"

# Who's been reading the secrets recently
aws cloudtrail lookup-events \
    --lookup-attributes AttributeKey=ResourceName,AttributeValue=/cert-ra/${env}/oauth/providers \
    --start-time "$(date -u -d '24 hours ago' --iso-8601=seconds)"
```

The `BaselineCloudTrail` in ObservabilityStack is configured for
account-wide capture and tamper-resistant retention.

---

## References

- Temporal mTLS rotation: [temporal-mtls-rotation.md](temporal-mtls-rotation.md)
- Seeding (initial values): [`infra/scripts/seed-secrets.py`](../infra/scripts/seed-secrets.py)
