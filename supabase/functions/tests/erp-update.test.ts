import { strict as assert } from "node:assert";
import { test } from "node:test";
import { recoveryHmacSha256Hex as hmac, recoverySha256Hex as hash } from "../_shared/erp-admin-recovery-contract.ts";
import { UPDATE_REQUEST_DOMAIN, UPDATE_RESPONSE_DOMAIN } from "../_shared/erp-update-contract.ts";
import { handleErpUpdate } from "../erp-update/handler.ts";

const secret = "q".repeat(43), tenant = "qa-company", installation = "qa-installation-001";
const now = 1791072000, timestamp = String(now);
const nonce = "11111111-1111-4111-8111-111111111111", requestId = "22222222-2222-4222-8222-222222222222";
const context = [tenant, installation, timestamp, nonce, requestId];
const origin = "https://abcdefghijklmnopqrst.supabase.co";
const env = { get: (name: string) => ({ SUPABASE_URL: origin, SUPABASE_SERVICE_ROLE_KEY: "CANARY_SERVER_ONLY_QA" })[name] };
const body = { schema_version: 1, tenant_id: tenant, installation_id: installation, installed_version: "1.1.0" };
const manifest = {
  schema_version: 1, release_id: "33333333-3333-4333-8333-333333333333", sequence: 2,
  version: "1.2.0", update_channel: "stable", platform: "windows", architecture: "x64",
  build_commit: "a".repeat(40), artifact_sha256: "b".repeat(64), artifact_size: 1024,
  release_notes: "QA only", minimum_supported_version: "1.0.0", mandatory: false,
  published_at: new Date((now - 60) * 1000).toISOString(), code_signing: "CODE_SIGNING_PENDING", signer_thumbprint: null,
};
const path = `/object/sign/nexpoint-releases/windows/x64/${manifest.artifact_sha256}.exe?token=${"synthetic-qa-download-token"}`;
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "content-type": "application/json" } }); }
async function request(value: Record<string, unknown> = body): Promise<Request> {
  const encoded = JSON.stringify(value);
  return new Request(origin + "/functions/v1/erp-update", { method: "POST", body: encoded, headers: {
    "content-type": "application/json", authorization: `Bearer ${secret}`,
    "x-nexpoint-tenant-id": tenant, "x-nexpoint-installation-id": installation,
    "x-nexpoint-timestamp": timestamp, "x-nexpoint-nonce": nonce, "x-request-id": requestId,
    "x-nexpoint-signature": `v1=${await hmac(secret, [UPDATE_REQUEST_DOMAIN, ...context, await hash(encoded)].join("\n"))}`,
  } });
}

test("scopes RPC by installation credential and signs bounded manifest/download", async () => {
  const calls: string[] = [];
  const fetcher: typeof fetch = async (input, init) => {
    calls.push(String(input));
    assert.equal(init?.redirect, "error");
    const value = JSON.parse(String(init?.body));
    if (calls.length === 1) {
      assert.equal(String(input), origin + "/rest/v1/rpc/np_erp_check_release");
      assert.equal(value.p_secret_hash, await hash(secret));
      assert.equal(value.p_tenant_id, tenant);
      assert.equal(value.p_installation_id, installation);
      assert.equal(value.p_installed_version, "1.1.0");
      assert.equal(value.secret, undefined);
      return json({ ok: true, update_channel: "stable", release: manifest });
    }
    assert.equal(String(input), origin + "/storage/v1" + path.split("?")[0]);
    assert.deepEqual(value, { expiresIn: 300 });
    return json({ signedURL: path });
  };
  const response = await handleErpUpdate(await request(), env, fetcher, now);
  assert.equal(response.status, 200);
  const encoded = await response.text(), result = JSON.parse(encoded);
  assert.deepEqual(result.release, manifest);
  assert.equal(result.download.url, origin + "/storage/v1" + path);
  assert.equal(response.headers.get("x-nexpoint-signature"), `v1=${await hmac(secret, [UPDATE_RESPONSE_DOMAIN, ...context, await hash(encoded)].join("\n"))}`);
  assert.equal(response.headers.get("cache-control"), "no-store");
  assert.equal(calls.length, 2);
  assert.ok(!encoded.includes("CANARY_SERVER_ONLY_QA") && !encoded.includes(secret));
});

test("no release performs no storage request", async () => {
  let calls = 0;
  const response = await handleErpUpdate(await request(), env, async () => {
    calls++; return json({ ok: true, update_channel: "internal", release: null });
  }, now);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).download, null);
  assert.equal(calls, 1);
});

for (const [name, change, status] of [
  ["wrong tenant", { tenant_id: "other-company" }, 403],
  ["wrong installation", { installation_id: "other-installation" }, 403],
  ["schema", { schema_version: 2 }, 400], ["extra privilege", { role: "admin" }, 400],
  ["version", { installed_version: "1.01.0" }, 400],
] as const) test(`rejects ${name} before remote access`, async () => {
  const response = await handleErpUpdate(await request({ ...body, ...change }), env,
    async () => { assert.fail("unexpected remote access"); }, now);
  assert.equal(response.status, status);
});

for (const [name, header, value, status] of [
  ["bad signature", "x-nexpoint-signature", `v1=${"0".repeat(64)}`, 401],
  ["stale timestamp", "x-nexpoint-timestamp", String(now - 90), 401],
  ["browser origin", "origin", origin, 405],
  ["privileged key", "authorization", "Bearer CANARY_SERVER_ONLY_QA", 401],
] as const) test(`rejects ${name}`, async () => {
  const req = await request(); req.headers.set(header, value);
  const response = await handleErpUpdate(req, env, async () => { assert.fail("unexpected remote access"); }, now);
  assert.equal(response.status, status);
});

for (const [code, status] of [["unauthorized", 401], ["replay", 409], ["rate_limited", 429]] as const) {
  test(`database ${code} is enforced before storage`, async () => {
    let calls = 0;
    const response = await handleErpUpdate(await request(), env, async () => { calls++; return json({ ok: false, code }); }, now);
    assert.equal(response.status, status); assert.equal(calls, 1);
  });
}

for (const change of [{ artifact_sha256: "../evil" }, { update_channel: "internal" }, { artifact_size: -1 },
    { signer_thumbprint: "A".repeat(40) }, { version: "1.0.0" }, { sequence: 0 }, { injected: true }]) {
  test(`invalid manifest ${Object.keys(change)[0]} cannot receive URL`, async () => {
    let calls = 0;
    const response = await handleErpUpdate(await request(), env, async () => {
      calls++; return json({ ok: true, update_channel: "stable", release: { ...manifest, ...change } });
    }, now);
    assert.equal(response.status, 502); assert.equal(calls, 1);
  });
}

for (const url of ["https://attacker.invalid/evil.exe", path + "&extra=true", path.replace("windows", "../windows")]) {
  test(`storage URL injection is rejected: ${url.slice(0, 25)}`, async () => {
    let calls = 0;
    const response = await handleErpUpdate(await request(), env, async () => ++calls === 1
      ? json({ ok: true, update_channel: "stable", release: manifest }) : json({ signedURL: url }), now);
    assert.equal(response.status, 502);
  });
}

test("transport errors never expose server exception", async () => {
  const response = await handleErpUpdate(await request(), env, async () => { throw new Error("CANARY_SERVER_ONLY_QA"); }, now);
  assert.equal(response.status, 503);
  assert.ok(!(await response.text()).includes("CANARY"));
});
