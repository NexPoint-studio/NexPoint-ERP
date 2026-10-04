export interface ActivationEnvironmentReader {
  get(name: string): string | undefined;
}

const runtimeEnvironment: ActivationEnvironmentReader = {
  get: (name) => Deno.env.get(name),
};
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
const IDENTIFIER = /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{7,119}$/;
const MAX_BODY = 4096;
class ActivationError extends Error {
  constructor(readonly code: string, readonly status: number) {
    super(code);
  }
}

interface ActivationRequest {
  schema_version: 2;
  activation_code: string;
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
    "schema_version", "activation_code", "tenant_key", "installation_key",
    "environment", "request_id", "key_id", "secret_digest",
  ];
  if (
    Object.keys(value).length !== keys.length || keys.some((key) => !(key in value)) ||
    value.schema_version !== 2 || value.environment !== "prod" ||
    typeof value.activation_code !== "string" || !/^[A-Za-z0-9_-]{43}$/.test(value.activation_code) ||
    typeof value.tenant_key !== "string" || value.tenant_key.length > 80 ||
    !IDENTIFIER.test(value.tenant_key) ||
    typeof value.installation_key !== "string" || !IDENTIFIER.test(value.installation_key) ||
    typeof value.request_id !== "string" || !UUID.test(value.request_id) ||
    typeof value.key_id !== "string" || !/^key_[0-9a-f]{64}$/.test(value.key_id) ||
    typeof value.secret_digest !== "string" || !/^[0-9a-f]{64}$/.test(value.secret_digest)
  ) throw new ActivationError("invalid_payload", 400);
  return value as unknown as ActivationRequest;
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
    const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body.activation_code)));
    const completed = await rpc(backend, fetcher, "np_consume_installation_code", {
      p_code_digest: [...digest].map((v) => v.toString(16).padStart(2, "0")).join(""),
      p_tenant_key: body.tenant_key, p_installation_key: body.installation_key,
      p_request_id: body.request_id, p_key_id: body.key_id, p_secret_digest: body.secret_digest,
    }, requestId);
    if (completed.ok !== true) throw remoteError(completed);
    if (
      completed.schema_version !== 2 || completed.request_id !== body.request_id ||
      completed.tenant_key !== body.tenant_key || completed.installation_key !== body.installation_key ||
      completed.key_id !== body.key_id || completed.environment !== "prod" ||
      !["internal", "customer"].includes(String(completed.tenant_kind)) ||
      ["company_name", "installation_label"].some((key) =>
        typeof completed[key] !== "string" || (completed[key] as string).length < 1 ||
        (completed[key] as string).length > (key === "company_name" ? 180 : 160) || /[\u0000-\u001f\u007f]/.test(completed[key] as string)
      )
    ) throw new ActivationError("service_unavailable", 503);
    return response(200, requestId, {
      schema_version: 2, ok: true, request_id: requestId,
      tenant_key: completed.tenant_key, tenant_kind: completed.tenant_kind,
      installation_key: completed.installation_key, environment: "prod",
      company_name: completed.company_name, installation_label: completed.installation_label,
      key_id: completed.key_id,
    });
  } catch (error) {
    const known = error instanceof ActivationError ? error : new ActivationError("internal_error", 500);
    // Never log request bodies, hashes, passwords, backend messages or headers.
    return response(known.status, requestId, { schema_version: 2, ok: false, code: known.code });
  }
}
