import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  canonicalRecoveryBody,
  recoveryHmacSha256Hex,
  recoveryRequestSignatureMessage,
  recoveryResponseSignatureMessage,
  recoverySha256Hex,
} from "../_shared/erp-admin-recovery-contract.ts";
import {
  handleErpAdminRecovery,
  type RecoveryEnvironmentReader,
} from "../erp-admin-recovery/handler.ts";

const secret = "r".repeat(43);
const tenantId = "tenant_prod_001";
const installationId = "installation_prod_001";
const timestamp = "1789910400";
const nonce = "11111111-1111-4111-8111-111111111111";
const requestId = "22222222-2222-4222-8222-222222222222";
const bodyValue = {
  schema_version: 1 as const,
  action: "status" as const,
  tenant_id: tenantId,
  installation_id: installationId,
  requester_ref: "requester_prod_001",
};
const environment: RecoveryEnvironmentReader = {
  get(name) {
    if (name === "SUPABASE_URL") return "https://example.supabase.co";
    if (name === "SUPABASE_SERVICE_ROLE_KEY") return "CANARY_SERVICE_ROLE_TEST_ONLY";
    return undefined;
  },
};

async function recoveryRequest(
  value: Record<string, unknown> = bodyValue,
  signatureBody: Record<string, unknown> = value,
): Promise<Request> {
  const signature = await recoveryHmacSha256Hex(
    secret,
    recoveryRequestSignatureMessage(
      tenantId,
      installationId,
      timestamp,
      nonce,
      requestId,
      await recoverySha256Hex(canonicalRecoveryBody(signatureBody as typeof bodyValue)),
    ),
  );
  return new Request("https://function.example/erp-admin-recovery", {
    method: "POST",
    headers: {
      authorization: `Bearer ${secret}`,
      "content-type": "application/json",
      "x-nexpoint-tenant-id": tenantId,
      "x-nexpoint-installation-id": installationId,
      "x-nexpoint-timestamp": timestamp,
      "x-nexpoint-nonce": nonce,
      "x-nexpoint-signature": `v1=${signature}`,
      "x-request-id": requestId,
    },
    body: JSON.stringify(value),
  });
}

test("authenticates status, delegates replay protection and signs the response", async () => {
  const authorization = {
    authorization_id: "33333333-3333-4333-8333-333333333333",
    tenant_id: tenantId,
    installation_id: installationId,
    ticket_id: "ticket_prod_001",
    authorized_by: "44444444-4444-4444-8444-444444444444",
    created_at: "2026-09-21T12:00:00Z",
    expires_at: "2026-09-21T12:15:00Z",
    consumed_at: null,
  };
  const fetcher: typeof fetch = async (input, init) => {
    assert.equal(
      String(input),
      "https://example.supabase.co/rest/v1/rpc/np_erp_access_reset_authorization",
    );
    const rpc = JSON.parse(String((init as { body?: unknown }).body));
    assert.equal(rpc.p_tenant_id, tenantId);
    assert.equal(rpc.p_installation_id, installationId);
    assert.equal(rpc.p_secret_hash, await recoverySha256Hex(secret));
    assert.equal(rpc.p_action, "status");
    assert.equal(rpc.p_authorization_id, null);
    assert.match(rpc.p_nonce_hash, /^[0-9a-f]{64}$/);
    return new Response(JSON.stringify({ ok: true, action: "status", authorization }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };

  const result = await handleErpAdminRecovery(
    await recoveryRequest(),
    environment,
    fetcher,
    Number(timestamp),
  );
  assert.equal(result.status, 200);
  const encoded = await result.text();
  assert.deepEqual(JSON.parse(encoded), {
    ok: true,
    request_id: requestId,
    authorization,
  });
  const expected = await recoveryHmacSha256Hex(
    secret,
    recoveryResponseSignatureMessage(
      tenantId,
      installationId,
      timestamp,
      nonce,
      requestId,
      await recoverySha256Hex(encoded),
    ),
  );
  assert.equal(result.headers.get("x-nexpoint-signature"), `v1=${expected}`);
  assert.equal(result.headers.get("x-nexpoint-timestamp"), timestamp);
  assert.equal(result.headers.get("x-nexpoint-nonce"), nonce);
  assert.equal(result.headers.get("cache-control"), "no-store");
});

test("rejects bad signatures and schema versions before the RPC", async () => {
  let calls = 0;
  const fetcher: typeof fetch = () => {
    calls += 1;
    return Promise.reject(new Error("must not run"));
  };
  const changed = { ...bodyValue, requester_ref: "changed_requester" };
  const badSignature = await handleErpAdminRecovery(
    await recoveryRequest(changed, bodyValue),
    environment,
    fetcher,
    Number(timestamp),
  );
  assert.equal(badSignature.status, 401);

  const badSchema = await handleErpAdminRecovery(
    await recoveryRequest({ ...bodyValue, schema_version: 2 }),
    environment,
    fetcher,
    Number(timestamp),
  );
  assert.equal(badSchema.status, 400);
  assert.equal(calls, 0);
});

test("maps persistent replay and rejects malformed authorization projections", async () => {
  const replay = await handleErpAdminRecovery(
    await recoveryRequest(),
    environment,
    () => Promise.resolve(new Response(JSON.stringify({ ok: false, code: "replay" }))),
    Number(timestamp),
  );
  assert.equal(replay.status, 409);
  assert.match(replay.headers.get("x-nexpoint-signature") ?? "", /^v1=[0-9a-f]{64}$/);

  const malformed = await handleErpAdminRecovery(
    await recoveryRequest(),
    environment,
    () =>
      Promise.resolve(
        new Response(JSON.stringify({
          ok: true,
          action: "status",
          authorization: { authorization_id: "not-a-uuid" },
        })),
      ),
    Number(timestamp),
  );
  assert.equal(malformed.status, 502);
});

test("consume vincula atomicamente a autorização assinada", async () => {
  const authorizationId = "33333333-3333-4333-8333-333333333333";
  const consumeBody = {
    ...bodyValue,
    action: "consume" as const,
    authorization_id: authorizationId,
  };
  const fetcher: typeof fetch = (_input, init) => {
    const rpc = JSON.parse(String((init as { body?: unknown }).body));
    assert.equal(rpc.p_action, "consume");
    assert.equal(rpc.p_authorization_id, authorizationId);
    return Promise.resolve(
      new Response(JSON.stringify({
        ok: true,
        action: "consume",
        authorization: {
          authorization_id: authorizationId,
          tenant_id: tenantId,
          installation_id: installationId,
          ticket_id: "ticket_prod_001",
          authorized_by: "44444444-4444-4444-8444-444444444444",
          created_at: "2026-09-21T12:00:00Z",
          expires_at: "2026-09-21T12:15:00Z",
          consumed_at: "2026-09-21T12:01:00Z",
        },
      })),
    );
  };
  const result = await handleErpAdminRecovery(
    await recoveryRequest(consumeBody),
    environment,
    fetcher,
    Number(timestamp),
  );
  assert.equal(result.status, 200);
  assert.equal((await result.json()).authorization.authorization_id, authorizationId);
});
