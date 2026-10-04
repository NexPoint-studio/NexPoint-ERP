import { test } from "node:test";
import { handleErpActivation } from "./handler.ts";
function assert(value: unknown, message = "assertion failed"): asserts value {
  if (!value) throw new Error(message);
}
const code = "A".repeat(43);
const env = { get: (name: string) => name === "SUPABASE_URL" ? "https://example.supabase.co" : "synthetic-service-credential" };
const body = {
  schema_version: 2, activation_code: code,
  tenant_key: "tenant-example", installation_key: "example-pc-01", environment: "prod",
  request_id: "11111111-1111-4111-8111-111111111111", key_id: "key_" + "b".repeat(64), secret_digest: "a".repeat(64),
};
const completed = {
  schema_version: 2, ok: true, request_id: body.request_id, tenant_key: body.tenant_key,
  installation_key: body.installation_key, key_id: body.key_id, tenant_kind: "customer", environment: "prod",
  company_name: "Example Company", installation_label: "Example PC",
};
function request(value: unknown = body, headers: Record<string, string> = {}) {
  return new Request("https://example.supabase.co/functions/v1/erp-activate", {
    method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(value),
  });
}

test("valid activation exposes only scoped metadata without any user verifier", async () => {
  const calls: string[] = [];
  const fetcher: typeof fetch = async (input, options) => {
    calls.push(String(input));
    assert(options?.redirect === "error");
    assert(!String(options?.body).includes(code));
    return Response.json({ ...completed, forbidden: "do-not-echo", password_hash: "forbidden-hash" });
  };
  const result = await handleErpActivation(request(), env, fetcher);
  assert(result.status === 200 && calls.length === 1);
  const text = await result.text();
  assert(!text.includes(code) && !text.includes("forbidden-hash") && !text.includes("do-not-echo"));
  assert(!("owner_password_hash" in JSON.parse(text)));
  assert(result.headers.get("cache-control") === "no-store");
});

for (const [name, change] of Object.entries({
  short_code: { activation_code: "short" }, wrong_environment: { environment: "qa" },
  malformed_digest: { secret_digest: "secret" }, wrong_schema: { schema_version: 1 },
  additional_secret: { installation_secret: "forbidden" }, invalid_request: { request_id: "bad" },
})) test(`invalid request ${name} never reaches backend`, async () => {
  let called = false;
  const result = await handleErpActivation(request({ ...body, ...change }), env, async () => { called = true; return Response.json({}); });
  assert(result.status === 400 && !called);
});

test("browser Origin and oversized request rejected", async () => {
  let called = false;
  const fetcher: typeof fetch = async () => { called = true; return Response.json({}); };
  assert((await handleErpActivation(request(body, { origin: "https://evil.invalid" }), env, fetcher)).status === 405);
  assert((await handleErpActivation(request({ ...body, activation_code: "x".repeat(5000) }), env, fetcher)).status === 413);
  assert(!called);
});

for (const code of ["unauthorized", "rate_limited"]) test(`preflight ${code} does not complete`, async () => {
  let calls = 0;
  const result = await handleErpActivation(request(), env, async () => { calls++; return Response.json({ ok: false, code }); });
  assert(calls === 1 && result.status === (code === "unauthorized" ? 401 : 429));
});

for (const [field, value] of Object.entries({ tenant_key: "wrong-tenant", installation_key: "wrong-device", environment: "qa", schema_version: 1, key_id: "invalid", company_name: "" })) {
  test(`invalid completion ${field} fail closed`, async () => {
    let calls = 0;
    const result = await handleErpActivation(request(), env, async () => Response.json({ ...completed, [field]: value }));
    assert(result.status === 503);
    assert(!(await result.text()).includes("password_hash"));
  });
}

for (const code of ["unauthorized", "activation_conflict"]) test(`revocation or replay ${code}`, async () => {
  let calls = 0;
  const result = await handleErpActivation(request(), env, async () => Response.json({ ok: false, code }));
  assert(result.status === (code === "unauthorized" ? 401 : 409));
});

test("network failure has sanitized error", async () => {
  const result = await handleErpActivation(request(), env, () => { throw new Error(code); });
  assert(result.status === 503 && !(await result.text()).includes(code));
});

test("company names match the 180 character company profile contract", async () => {
  for (const [length, status] of [[180, 200], [181, 503]]) {
    const result = await handleErpActivation(request(), env, async () => Response.json({ ...completed, company_name: "C".repeat(length) }));
    assert(result.status === status);
  }
});
