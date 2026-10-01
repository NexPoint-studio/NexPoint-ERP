import { Buffer } from "node:buffer";
import { scryptSync } from "node:crypto";
import { handleErpActivation, verifyActivationPassword } from "./handler.ts";

function assert(value: unknown, message = "assertion failed"): asserts value {
  if (!value) throw new Error(message);
}
const password = "synthetic-operator-password-2026";
function hash(value: string, saltByte: number) {
  const salt = Buffer.alloc(16, saltByte);
  return `scrypt$16384$8$1$${salt.toString("base64").replaceAll("+", "-").replaceAll("/", "_")}$${scryptSync(value, salt, 32, { N: 16384, r: 8, p: 1 }).toString("base64").replaceAll("+", "-").replaceAll("/", "_")}`;
}
const operatorHash = hash(password, 1);
const ownerHash = hash("synthetic-distinct-owner-password-2026", 2);
const env = { get: (name: string) => name === "SUPABASE_URL" ? "https://example.supabase.co" : "synthetic-service-credential" };
const body = {
  schema_version: 1, username: "operator.example", password,
  tenant_key: "tenant-example", installation_key: "example-pc-01", environment: "prod",
  request_id: "11111111-1111-4111-8111-111111111111", key_id: "key_" + "b".repeat(64), secret_digest: "a".repeat(64),
};
const grant = { ok: true, grant_id: "22222222-2222-4222-8222-222222222222", auth_version: "33333333-3333-4333-8333-333333333333", password_hash: operatorHash };
const completed = {
  schema_version: 1, ok: true, request_id: body.request_id, tenant_key: body.tenant_key,
  installation_key: body.installation_key, key_id: body.key_id, tenant_kind: "internal", environment: "prod",
  username: body.username, display_name: "Example Operator", company_name: "Example Company",
  installation_label: "Example PC", role: "user", owner_username: "owner.example",
  owner_display_name: "Example Owner", owner_password_hash: ownerHash,
};
function request(value: unknown = body, headers: Record<string, string> = {}) {
  return new Request("https://example.supabase.co/functions/v1/erp-activate", {
    method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(value),
  });
}

Deno.test("scrypt matches Python format and fixed work; no downgrade", async () => {
  assert(await verifyActivationPassword(password, operatorHash));
  assert(!await verifyActivationPassword("wrong-password", operatorHash));
  assert(!await verifyActivationPassword(password, operatorHash.replace("16384", "2")));
});

Deno.test("valid activation exposes only authorized local verifier and scoped metadata", async () => {
  const calls: string[] = [];
  const fetcher: typeof fetch = async (input, options) => {
    calls.push(String(input));
    assert(options?.redirect === "error");
    assert(!String(options?.body).includes(password));
    return Response.json(calls.length === 1 ? grant : { ...completed, forbidden: "do-not-echo", password_hash: operatorHash });
  };
  const result = await handleErpActivation(request(), env, fetcher);
  assert(result.status === 200 && calls.length === 2);
  const text = await result.text();
  assert(!text.includes(password) && !text.includes(operatorHash) && !text.includes("do-not-echo"));
  assert(JSON.parse(text).owner_password_hash === ownerHash);
  assert(result.headers.get("cache-control") === "no-store");
});

for (const [name, change] of Object.entries({
  short_password: { password: "short" }, wrong_environment: { environment: "qa" },
  malformed_digest: { secret_digest: "secret" }, wrong_schema: { schema_version: 2 },
  additional_secret: { installation_secret: "forbidden" }, invalid_request: { request_id: "bad" },
})) Deno.test(`invalid request ${name} never reaches backend`, async () => {
  let called = false;
  const result = await handleErpActivation(request({ ...body, ...change }), env, async () => { called = true; return Response.json({}); });
  assert(result.status === 400 && !called);
});

Deno.test("browser Origin and oversized request rejected", async () => {
  let called = false;
  const fetcher: typeof fetch = async () => { called = true; return Response.json({}); };
  assert((await handleErpActivation(request(body, { origin: "https://evil.invalid" }), env, fetcher)).status === 405);
  assert((await handleErpActivation(request({ ...body, password: "x".repeat(5000) }), env, fetcher)).status === 413);
  assert(!called);
});

for (const code of ["unauthorized", "rate_limited"]) Deno.test(`preflight ${code} does not complete`, async () => {
  let calls = 0;
  const result = await handleErpActivation(request(), env, async () => { calls++; return Response.json({ ok: false, code }); });
  assert(calls === 1 && result.status === (code === "unauthorized" ? 401 : 429));
});

Deno.test("wrong password never completes", async () => {
  let calls = 0;
  const result = await handleErpActivation(request({ ...body, password: "wrong-but-long-password" }), env, async () => { calls++; return Response.json(grant); });
  assert(calls === 1 && result.status === 401);
});

for (const [field, value] of Object.entries({ tenant_key: "wrong-tenant", installation_key: "wrong-device", environment: "qa", role: "admin", owner_username: "nexpoint-admin", owner_password_hash: "plaintext" })) {
  Deno.test(`invalid completion ${field} fail closed`, async () => {
    let calls = 0;
    const result = await handleErpActivation(request(), env, async () => Response.json(++calls === 1 ? grant : { ...completed, [field]: value }));
    assert(result.status === 503);
    assert(!(await result.text()).includes(ownerHash));
  });
}

for (const code of ["unauthorized", "activation_conflict"]) Deno.test(`revocation or replay ${code}`, async () => {
  let calls = 0;
  const result = await handleErpActivation(request(), env, async () => Response.json(++calls === 1 ? grant : { ok: false, code }));
  assert(result.status === (code === "unauthorized" ? 401 : 409));
});

Deno.test("network failure has sanitized error", async () => {
  const result = await handleErpActivation(request(), env, () => { throw new Error(password); });
  assert(result.status === 503 && !(await result.text()).includes(password));
});
