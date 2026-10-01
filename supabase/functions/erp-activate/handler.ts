import { Buffer } from "node:buffer";
import { scrypt, timingSafeEqual } from "node:crypto";

export interface ActivationEnvironmentReader {
  get(name: string): string | undefined;
}

const runtimeEnvironment: ActivationEnvironmentReader = {
  get: (name) => Deno.env.get(name),
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const IDENTIFIER = /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{7,119}$/;
const MAX_BODY = 4096;
// Synthetic, fixed-work verifier used for absent/unauthorized accounts. It is
// never accepted as an authorization and never stored in a grant.
const DUMMY_HASH = "scrypt$16384$8$1$AAAAAAAAAAAAAAAAAAAAAA==$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=";
const SCRYPT_HASH = /^scrypt\$16384\$8\$1\$[A-Za-z0-9_-]{22}==\$[A-Za-z0-9_-]{43}=$/;

class ActivationError extends Error {
  constructor(readonly code: string, readonly status: number) {
    super(code);
  }
}

interface ActivationRequest {
  schema_version: 1;
  username: string;
  password: string;
  tenant_key: string;
  installation_key: string;
  environment: "prod";
  request_id: string;
  key_id: string;
  secret_digest: string;
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ActivationError("invalid_payload", 400);
  }
  return value as Record<string, unknown>;
}

async function readBody(request: Request): Promise<ActivationRequest> {
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > MAX_BODY)) {
    throw new ActivationError("invalid_payload", 413);
  }
  if (!request.body) throw new ActivationError("invalid_payload", 400);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.length;
      if (size > MAX_BODY) {
        await reader.cancel();
        throw new ActivationError("invalid_payload", 413);
      }
      chunks.push(chunk.value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, offset);
    offset += chunk.length;
  }
  let value: Record<string, unknown>;
  try {
    value = object(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
  } catch {
    throw new ActivationError("invalid_payload", 400);
  }
  const keys = [
    "schema_version", "username", "password", "tenant_key", "installation_key",
    "environment", "request_id", "key_id", "secret_digest",
  ];
  if (
    Object.keys(value).length !== keys.length || keys.some((key) => !(key in value)) ||
    value.schema_version !== 1 || value.environment !== "prod" ||
    typeof value.username !== "string" ||
    !/^[a-z0-9][a-z0-9_.@+-]{2,159}$/.test(value.username) ||
    typeof value.password !== "string" || [...value.password].length < 8 ||
    [...value.password].length > 256 || value.password.includes("\0") ||
    typeof value.tenant_key !== "string" || value.tenant_key.length > 80 ||
    !IDENTIFIER.test(value.tenant_key) ||
    typeof value.installation_key !== "string" || !IDENTIFIER.test(value.installation_key) ||
    typeof value.request_id !== "string" || !UUID.test(value.request_id) ||
    typeof value.key_id !== "string" || !/^key_[0-9a-f]{64}$/.test(value.key_id) ||
    typeof value.secret_digest !== "string" || !/^[0-9a-f]{64}$/.test(value.secret_digest)
  ) throw new ActivationError("invalid_payload", 400);
  return value as unknown as ActivationRequest;
}

// Identical parameters/encoding to app/core/security.py; no caller-controlled
// KDF cost, no downgrade, and constant-time comparison of the derived bytes.
export async function verifyActivationPassword(password: string, encoded: string): Promise<boolean> {
  const match = /^scrypt\$16384\$8\$1\$([A-Za-z0-9_-]{22}==)\$([A-Za-z0-9_-]{43}=)$/.exec(encoded);
  if (!match) return false;
  const salt = Buffer.from(match[1], "base64url");
  const expected = Buffer.from(match[2], "base64url");
  if (salt.length !== 16 || expected.length !== 32) return false;
  const derived = await new Promise<Buffer>((resolve, reject) => {
    scrypt(password, salt, 32, { N: 16384, r: 8, p: 1, maxmem: 32 * 1024 * 1024 }, (error, key) => {
      if (error) reject(error);
      else resolve(key);
    });
  });
  return timingSafeEqual(derived, expected);
}

function configuredBackend(reader: ActivationEnvironmentReader): { base: URL; key: string } {
  const raw = reader.get("SUPABASE_URL");
  const key = reader.get("SUPABASE_SERVICE_ROLE_KEY");
  if (!raw || !key) throw new ActivationError("service_unavailable", 503);
  let base: URL;
  try {
    base = new URL(raw);
  } catch {
    throw new ActivationError("service_unavailable", 503);
  }
  if (
    base.username || base.password || base.search || base.hash || base.pathname !== "/" ||
    (base.protocol !== "https:" &&
      !(base.protocol === "http:" && ["127.0.0.1", "localhost", "kong"].includes(base.hostname)))
  ) throw new ActivationError("service_unavailable", 503);
  return { base, key };
}

async function rpc(
  backend: { base: URL; key: string },
  fetcher: typeof fetch,
  name: string,
  payload: Record<string, unknown>,
  requestId: string,
): Promise<Record<string, unknown>> {
  try {
    const headers: Record<string, string> = {
      apikey: backend.key,
      "content-type": "application/json",
      "x-request-id": requestId,
    };
    if (!backend.key.startsWith("sb_secret_")) headers.authorization = `Bearer ${backend.key}`;
    const response = await fetcher(new URL(`/rest/v1/rpc/${name}`, backend.base), {
      method: "POST", headers, body: JSON.stringify(payload),
      redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("backend rejected");
    const text = await response.text();
    if (text.length > 8192) throw new Error("backend response too large");
    return object(JSON.parse(text));
  } catch {
    throw new ActivationError("service_unavailable", 503);
  }
}

function remoteError(value: Record<string, unknown>): ActivationError {
  if (value.code === "rate_limited") return new ActivationError("rate_limited", 429);
  if (value.code === "activation_conflict") return new ActivationError("activation_conflict", 409);
  if (value.code === "unauthorized") return new ActivationError("unauthorized", 401);
  return new ActivationError("service_unavailable", 503);
}

function response(status: number, requestId: string, value: Record<string, unknown>): Response {
  const headers = new Headers({
    "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
    "referrer-policy": "no-referrer", "x-content-type-options": "nosniff",
    "x-frame-options": "DENY", "x-request-id": requestId,
  });
  if (status === 429) headers.set("retry-after", "900");
  return new Response(JSON.stringify(value), { status, headers });
}

export async function handleErpActivation(
  request: Request,
  reader: ActivationEnvironmentReader = runtimeEnvironment,
  fetcher: typeof fetch = fetch,
): Promise<Response> {
  let requestId: string = crypto.randomUUID();
  try {
    // Desktop-only endpoint: no browser origin, CORS, cookies or query secrets.
    if (request.method !== "POST" || request.headers.has("origin")) {
      throw new ActivationError("method_not_allowed", 405);
    }
    if (new URL(request.url).search) throw new ActivationError("invalid_payload", 400);
    if (request.headers.get("content-type")?.split(";", 1)[0].trim() !== "application/json") {
      throw new ActivationError("unsupported_media_type", 415);
    }
    const body = await readBody(request);
    requestId = body.request_id;
    const backend = configuredBackend(reader);
    const scope = {
      p_username: body.username, p_tenant_key: body.tenant_key,
      p_installation_key: body.installation_key, p_environment: body.environment,
    };
    const prepared = await rpc(backend, fetcher, "np_prepare_installation_activation", scope, requestId);
    if (prepared.ok !== true) {
      if (prepared.code === "unauthorized") await verifyActivationPassword(body.password, DUMMY_HASH);
      throw remoteError(prepared);
    }
    if (
      typeof prepared.grant_id !== "string" || !UUID.test(prepared.grant_id) ||
      typeof prepared.auth_version !== "string" || !UUID.test(prepared.auth_version) ||
      typeof prepared.password_hash !== "string"
    ) throw new ActivationError("service_unavailable", 503);
    if (!await verifyActivationPassword(body.password, prepared.password_hash)) {
      throw new ActivationError("unauthorized", 401);
    }
    const completed = await rpc(backend, fetcher, "np_complete_installation_activation", {
      ...scope, p_grant_id: prepared.grant_id, p_auth_version: prepared.auth_version,
      p_request_id: body.request_id, p_key_id: body.key_id, p_secret_digest: body.secret_digest,
    }, requestId);
    if (completed.ok !== true) throw remoteError(completed);
    if (
      completed.schema_version !== 1 || completed.request_id !== body.request_id ||
      completed.tenant_key !== body.tenant_key || completed.installation_key !== body.installation_key ||
      completed.username !== body.username || completed.key_id !== body.key_id ||
      completed.environment !== "prod" || completed.role !== "user" ||
      !["internal", "customer"].includes(String(completed.tenant_kind)) ||
      typeof completed.owner_username !== "string" ||
      !/^[a-z0-9][a-z0-9_.@+-]{2,159}$/.test(completed.owner_username) ||
      [body.username, "nexpoint-admin"].includes(completed.owner_username) ||
      typeof completed.owner_password_hash !== "string" || !SCRYPT_HASH.test(completed.owner_password_hash) ||
      ["company_name", "installation_label", "display_name", "owner_display_name"].some((key) =>
        typeof completed[key] !== "string" || (completed[key] as string).length < 1 ||
        (completed[key] as string).length > 160 || /[\u0000-\u001f\u007f]/.test(completed[key] as string)
      )
    ) throw new ActivationError("service_unavailable", 503);
    // Explicit projection: only the authorized local owner verifier is exported;
    // no operator hash, plaintext password or administrative cloud key is echoed.
    return response(200, requestId, {
      schema_version: 1, ok: true, request_id: requestId,
      tenant_key: completed.tenant_key, tenant_kind: completed.tenant_kind,
      installation_key: completed.installation_key, environment: "prod",
      company_name: completed.company_name, installation_label: completed.installation_label,
      key_id: completed.key_id, username: completed.username, display_name: completed.display_name,
      role: "user", owner_username: completed.owner_username,
      owner_display_name: completed.owner_display_name, owner_password_hash: completed.owner_password_hash,
    });
  } catch (error) {
    const known = error instanceof ActivationError ? error : new ActivationError("internal_error", 500);
    // Never log request bodies, hashes, passwords, backend messages or headers.
    return response(known.status, requestId, { schema_version: 1, ok: false, code: known.code });
  }
}
