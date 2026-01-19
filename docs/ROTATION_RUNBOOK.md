# KEK Rotation Runbook

This guide describes the procedure for rotating Key Encryption Keys (KEKs) used for envelope encryption of upstream credentials in the Talos AI Gateway.

## Lifecycle of a KEK

1.  **Generation**: A new 32-byte key is generated and added to the environment as `TALOS_KEK_<new_id>`.
2.  **Promotion**: The `TALOS_CURRENT_KEK_ID` is updated to `<new_id>`. The gateway is restarted. New secrets and updates now use the new KEK.
3.  **Rotation (Re-wrap)**: Trigger the `rotate-all` administrative job to re-encrypt existing secrets with the new KEK.
4.  **Verification**: Monitor `kek-status` until stale counts for old KEKs reach 0.
5.  **Grace Period**: Wait for all instances to be updated and any long-running operations to complete.
6.  **Retirement**: Remove the old KEK from the environment.

## Step-by-Step Procedure

### 1. Provision New KEK

Generate a random 32-byte key and encode it as Base64URL (no padding).

```bash
openssl rand -base64 32 | tr '+/' '-_' | tr -d '='
```

Add to env:

```bash
TALOS_KEK_v2="<new_key_b64u>"
```

### 2. Promote to Current

Update `TALOS_CURRENT_KEK_ID` and restart the gateway service.

```bash
TALOS_CURRENT_KEK_ID="v2"
```

### 3. Trigger Global Rotation

Invoke the admin API to start background re-wrapping.

```bash
curl -X POST http://gateway/admin/v1/secrets/rotate-all \
     -H "Authorization: Bearer <root_token>"
```

Expected response `202 Accepted` with an `op_id` and `status_url`.

### 4. Monitor Progress

Check the status of the rotation operation.

```bash
curl http://gateway/admin/v1/secrets/rotation-status/<op_id>
```

Also check the overall stale counts:

```bash
curl http://gateway/admin/v1/secrets/kek-status
```

### 5. Finalize Retirement

Once `stale_counts["v1"]` is 0, remove `TALOS_KEK_v1` from the environment and restart.

> [!IMPORTANT]
> Do NOT remove an old KEK until all secrets using it have been rotated. Use the `kek-status` API to confirm.

## Troubleshooting

- **CAS Failure**: If a secret is updated manually during rotation, the worker might report a CAS failure. This is safe; the secret is already "fresh" or will be picked up in the next pass.
- **Decryption Error**: If an old KEK is removed too early, secrets using it will become unreadable. Re-add the old KEK immediately to restore service.
