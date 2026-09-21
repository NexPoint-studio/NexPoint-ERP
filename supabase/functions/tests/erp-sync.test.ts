import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  hmacSha256Hex,
  parseSyncRequest,
  sha256Hex,
  signatureMessage,
} from "../_shared/erp-sync-contract.ts";
import { type EnvironmentReader, handleErpSync } from "../erp-sync/handler.ts";

const secret = "a".repeat(43);
const installationId = "installation_prod_001";
const timestamp = "1789910400";
const nonce = "b".repeat(64);
const payload = {
  schema_version: 1 as const,
  items: [{
    event_type: "heartbeat",
    aggregate_type: "installation",
    aggregate_id: "heartbeat_prod_001",
    payload: {
      tenant_id: "tenant_prod_001",
      installation_id: installationId,
      environment: "production",
      health: "healthy",
    },
    schema_version: 1,
    idempotency_key: "heartbeat:prod:001",
  }],
};
const body = JSON.stringify(payload);
const environment: EnvironmentReader = {
  get(name) {
    if (name === "SUPABASE_URL") return "https://example.supabase.co";
    if (name === "SUPABASE_SERVICE_ROLE_KEY") return "CANARY_SERVICE_ROLE_TEST_ONLY";
    return undefined;
  },
};

async function request(overrides: Record<string, string> = {}): Promise<Request> {
  const signature = await hmacSha256Hex(
    secret,
    signatureMessage(installationId, timestamp, nonce, await sha256Hex(body)),
  );
  return new Request("https://function.example/erp-sync", {
    method: "POST",
    headers: {
      authorization: `Bearer ${secret}`,
      "content-type": "application/json",
      "x-nexpoint-installation-id": installationId,
      "x-nexpoint-nonce": nonce,
      "x-nexpoint-signature": `v1=${signature}`,
      "x-nexpoint-timestamp": timestamp,
      ...overrides,
    },
    body,
  });
}

test("accepts a signed bounded batch and returns only validated ACKs", async () => {
  const fetcher: typeof fetch = async (_input, init) => {
    const rpc = JSON.parse(String((init as { body?: unknown } | undefined)?.body));
    assert.equal(rpc.p_installation_id, installationId);
    assert.equal(rpc.p_secret_hash, await sha256Hex(secret));
    assert.equal(rpc.p_envelopes.length, 1);
    return new Response(
      JSON.stringify({
        ok: true,
        acks: [{
          idempotency_key: "heartbeat:prod:001",
          remote_id: "sync_prod_001",
          schema_version: 1,
          duplicate: false,
        }],
      }),
      { status: 200, headers: { "content-type": "application/json" } },
    );
  };
  const result = await handleErpSync(await request(), environment, fetcher, Number(timestamp));
  assert.equal(result.status, 200);
  assert.deepEqual(await result.json(), {
    ok: true,
    acks: [{
      idempotency_key: "heartbeat:prod:001",
      remote_id: "sync_prod_001",
      schema_version: 1,
      duplicate: false,
    }],
  });
});

test("rejects a changed body before contacting the database", async () => {
  let calls = 0;
  const signed = await request();
  const changed = new Request(signed.url, {
    method: "POST",
    headers: signed.headers,
    body: body.replace("healthy", "critical"),
  });
  const result = await handleErpSync(
    changed,
    environment,
    () => {
      calls += 1;
      return Promise.reject(new Error("must not run"));
    },
    Number(timestamp),
  );
  assert.equal(result.status, 401);
  assert.equal(calls, 0);
});

test("rejects stale requests, browser origins and sensitive payload keys", async () => {
  const stale = await handleErpSync(
    await request(),
    environment,
    fetch,
    Number(timestamp) + 301,
  );
  assert.equal(stale.status, 401);
  const browser = await handleErpSync(
    await request({ origin: "https://attacker.example" }),
    environment,
    fetch,
    Number(timestamp),
  );
  assert.equal(browser.status, 405);
  assert.throws(
    () =>
      parseSyncRequest({
        ...payload,
        items: [{
          ...payload.items[0],
          payload: { ...payload.items[0].payload, password: "CANARY_NOT_REAL" },
        }],
      }, installationId),
    /sensitive_payload_rejected/,
  );
  for (
    const invalid of [
      { ...payload.items[0], schema_version: 2 },
      {
        ...payload.items[0],
        payload: { ...payload.items[0].payload, environment: "qa" },
      },
      {
        ...payload.items[0],
        payload: { ...payload.items[0].payload, channel: "dev" },
      },
    ]
  ) {
    assert.throws(
      () => parseSyncRequest({ ...payload, items: [invalid] }, installationId),
      /invalid_payload/,
    );
  }
});

test("maps replay and rate limits without returning database details", async () => {
  for (const [code, expected] of [["replay", 409], ["rate_limited", 429]] as const) {
    const result = await handleErpSync(
      await request(),
      environment,
      () =>
        Promise.resolve(
          new Response(JSON.stringify({ ok: false, code }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        ),
      Number(timestamp),
    );
    assert.equal(result.status, expected);
    const parsed = await result.json() as Record<string, unknown>;
    assert.equal(parsed.ok, false);
    assert.equal(typeof parsed.code, "string");
  }
});
