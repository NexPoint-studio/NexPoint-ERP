import { strict as assert } from "node:assert";
import { test } from "node:test";
import { recoveryHmacSha256Hex as hmac, recoverySha256Hex as hash } from "../_shared/erp-admin-recovery-contract.ts";
import { HANDOFF_REQUEST_DOMAIN, HANDOFF_RESPONSE_DOMAIN } from "../_shared/erp-handoff-contract.ts";
import { handleErpHandoff } from "../erp-handoff/handler.ts";

const secret = "q".repeat(43), tenant = "source-qa", installation = "device-qa-001";
const target = "target-qa", operation = "33333333-3333-4333-8333-333333333333";
const now = 1791072000, timestamp = String(now);
const nonce = "11111111-1111-4111-8111-111111111111";
const requestId = "22222222-2222-4222-8222-222222222222";
const origin = "https://abcdefghijklmnopqrst.supabase.co";
const env = { get: (name: string) => ({ SUPABASE_URL: origin,
  SUPABASE_SERVICE_ROLE_KEY: "CANARY_SERVER_ONLY_QA" })[name] };
const receipt = { ok: true, schema_version: 1, operation_id: operation,
  installation_id: installation, source_tenant_id: tenant, target_tenant_id: target,
  target_tenant_kind: "customer", company_name: "Nil QA", installation_label: "PC Nil QA",
  backup_sha256: "a".repeat(64), local_preflight_sha256: "b".repeat(64),
  state: "cloud_committed" };

async function signedRequest(action = "receipt", overrides: Record<string, unknown> = {},
                             scope = tenant): Promise<Request> {
  const body = JSON.stringify({ schema_version: 1, action, tenant_id: scope,
    installation_id: installation, operation_id: operation, ...overrides });
  const context = [scope, installation, timestamp, nonce, requestId];
  return new Request(origin + "/functions/v1/erp-handoff", { method: "POST", body,
    headers: { "content-type": "application/json", authorization: `Bearer ${secret}`,
      "x-nexpoint-tenant-id": scope, "x-nexpoint-installation-id": installation,
      "x-nexpoint-timestamp": timestamp, "x-nexpoint-nonce": nonce,
      "x-request-id": requestId,
      "x-nexpoint-signature": `v1=${await hmac(secret,
        [HANDOFF_REQUEST_DOMAIN, ...context, await hash(body)].join("\n"))}` },
  });
}

test("bounded signed receipt uses credential digest, never privileged key on desktop", async () => {
  let called = 0;
  const fetcher: typeof fetch = async (input, init) => {
    called++;
    assert.equal(String(input), origin + "/rest/v1/rpc/np_erp_handoff_receipt");
    assert.equal(init?.redirect, "error");
    const payload = JSON.parse(String(init?.body));
    assert.equal(payload.p_secret_hash, await hash(secret));
    assert.equal(payload.p_presented_tenant_key, tenant);
    assert.equal(payload.p_installation_key, installation);
    assert.equal(payload.p_operation_id, operation);
    assert.equal(payload.p_confirm, false);
    assert(!String(init?.body).includes(secret));
    return Response.json({ ...receipt, extra_private_canary: "must-not-echo" });
  };
  const invalid = await handleErpHandoff(await signedRequest(), env, fetcher, now);
  assert.equal(invalid.status, 502);
  assert.equal(called, 1);
  const valid = await handleErpHandoff(await signedRequest(), env,
    async () => Response.json(receipt), now);
  assert.equal(valid.status, 200);
  const encoded = await valid.text();
  assert.deepEqual(JSON.parse(encoded).receipt, receipt);
  assert(!encoded.includes(secret) && !encoded.includes("CANARY_SERVER_ONLY_QA"));
  const context = [tenant, installation, timestamp, nonce, requestId];
  assert.equal(valid.headers.get("x-nexpoint-signature"), `v1=${await hmac(secret,
    [HANDOFF_RESPONSE_DOMAIN, ...context, await hash(encoded)].join("\n"))}`);
});

test("tampered, extra, cross-scope and browser requests stop before RPC", async () => {
  let calls = 0;
  const never: typeof fetch = async () => { calls++; return Response.json(receipt); };
  for (const request of [
    await signedRequest("receipt", { extra_secret: "CANARY" }),
    await signedRequest("receipt", { tenant_id: target }),
    await signedRequest("delete"),
    new Request(origin + "/functions/v1/erp-handoff", { method: "POST",
      body: "{}", headers: { origin: "https://attacker.example", "content-type": "application/json" } }),
  ]) {
    const result = await handleErpHandoff(request, env, never, now);
    assert.notEqual(result.status, 200);
  }
  assert.equal(calls, 0);
});

test("confirmation requires matching confirmed receipt and has separate action", async () => {
  const request = await signedRequest("confirm", {}, target);
  let seen = false;
  const fetcher: typeof fetch = async (_, init) => {
    const payload = JSON.parse(String(init?.body));
    assert.equal(payload.p_confirm, true);
    assert.equal(payload.p_presented_tenant_key, target);
    seen = true;
    return Response.json({ ...receipt, state: "confirmed" });
  };
  const result = await handleErpHandoff(request, env, fetcher, now);
  assert.equal(result.status, 200);
  assert(seen);
  const stale = await handleErpHandoff(await signedRequest("confirm", {}, target), env,
    async () => Response.json(receipt), now);
  assert.equal(stale.status, 502);
});

test("oversized RPC response and missing server config fail closed", async () => {
  const oversized = await handleErpHandoff(await signedRequest(), env,
    async () => new Response("x".repeat(9000), {
      headers: { "content-type": "application/json" } }), now);
  assert.equal(oversized.status, 502);
  const unavailable = await handleErpHandoff(await signedRequest(), { get: () => undefined },
    async () => { throw new Error("never"); }, now);
  assert.equal(unavailable.status, 503);
});
