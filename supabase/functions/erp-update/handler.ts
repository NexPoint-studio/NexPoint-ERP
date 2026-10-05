import {
  equalRecoveryHex as equalHex, parseInstallationId, parseRecoveryAuthorization, parseRecoverySignature,
  parseRecoveryTimestamp, parseRecoveryUuid, parseTenantId, readRecoveryBody, RecoveryContractError,
  recoveryHmacSha256Hex as hmac, recoverySha256Hex as hash,
} from "../_shared/erp-admin-recovery-contract.ts";
import { canonicalUpdateBody, CHANNELS, record, UPDATE_REQUEST_DOMAIN, UPDATE_RESPONSE_DOMAIN,
  validateUpdateManifest } from "../_shared/erp-update-contract.ts";

export interface UpdateEnvironment { get(name: string): string | undefined; }
const environment: UpdateEnvironment = { get: (name) => Deno.env.get(name) };
const QA_INTERNAL_ORIGIN = "http://kong:8000";
const QA_PUBLIC_ORIGIN = "https://qa.nexpoint.test:56430";

function updateOrigins(reader: UpdateEnvironment): { internal: URL; public: URL; qa: boolean } {
  const rawBase = reader.get("SUPABASE_URL");
  const qaMode = reader.get("NEXPOINT_QA_MODE");
  const qaPublicOrigin = reader.get("NEXPOINT_QA_PUBLIC_ORIGIN");
  if (!rawBase) throw new RecoveryContractError("service_unavailable", 503);
  if (qaMode !== undefined || qaPublicOrigin !== undefined) {
    if (qaMode !== "isolated" || qaPublicOrigin !== QA_PUBLIC_ORIGIN || rawBase !== QA_INTERNAL_ORIGIN) {
      throw new RecoveryContractError("service_unavailable", 503);
    }
    return { internal: new URL(QA_INTERNAL_ORIGIN), public: new URL(QA_PUBLIC_ORIGIN), qa: true };
  }
  const base = new URL(rawBase);
  if (base.username || base.password || base.search || base.hash || (base.pathname !== "/" && base.pathname !== "") ||
      (base.protocol !== "https:" && !(base.protocol === "http:" && ["127.0.0.1", "localhost"].includes(base.hostname)))) {
    throw new RecoveryContractError("service_unavailable", 503);
  }
  return { internal: base, public: base, qa: false };
}

async function boundedJson(response: Response): Promise<unknown> {
  if (!response.ok || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
    throw new RecoveryContractError("service_unavailable", 503);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new RecoveryContractError("invalid_remote_response", 502);
  let length = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      length += value.length;
      if (length > 32768) { await reader.cancel(); throw new RecoveryContractError("invalid_remote_response", 502); }
      chunks.push(value);
    }
    const result = new Uint8Array(length);
    let offset = 0;
    for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
    return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(result));
  } finally { reader.releaseLock(); }
}

export async function handleErpUpdate(request: Request, reader: UpdateEnvironment = environment,
                                    fetcher: typeof fetch = fetch, now = Math.floor(Date.now() / 1000)): Promise<Response> {
  const headers = new Headers({ "content-type": "application/json; charset=utf-8", "cache-control": "no-store",
    "x-content-type-options": "nosniff", "referrer-policy": "no-referrer", "x-frame-options": "DENY" });
  try {
    if (request.method !== "POST" || request.headers.has("origin")) throw new RecoveryContractError("method_not_allowed", 405);
    if (request.headers.get("content-type")?.split(";", 1)[0]?.trim() !== "application/json") {
      throw new RecoveryContractError("unsupported_media_type", 415);
    }
    const tenant = parseTenantId(request.headers.get("x-nexpoint-tenant-id"));
    const installation = parseInstallationId(request.headers.get("x-nexpoint-installation-id"));
    const requestId = parseRecoveryUuid(request.headers.get("x-request-id"));
    const nonce = parseRecoveryUuid(request.headers.get("x-nexpoint-nonce"));
    const timestamp = request.headers.get("x-nexpoint-timestamp") ?? "";
    parseRecoveryTimestamp(timestamp, now);
    const secret = parseRecoveryAuthorization(request.headers.get("authorization"));
    const signature = parseRecoverySignature(request.headers.get("x-nexpoint-signature"));
    const text = new TextDecoder("utf-8", { fatal: true }).decode(await readRecoveryBody(request));
    let body: unknown;
    try { body = JSON.parse(text); } catch { throw new RecoveryContractError("invalid_payload", 400); }
    if (text !== canonicalUpdateBody(body, tenant, installation)) throw new RecoveryContractError("invalid_payload", 400);
    const context = [tenant, installation, timestamp, nonce, requestId];
    if (!equalHex(await hmac(secret, [UPDATE_REQUEST_DOMAIN, ...context, await hash(text)].join("\n")), signature)) {
      throw new RecoveryContractError("unauthorized", 401);
    }
    const serviceKey = reader.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!serviceKey) throw new RecoveryContractError("service_unavailable", 503);
    const origins = updateOrigins(reader);
    const serverHeaders: Record<string, string> = { apikey: serviceKey, "content-type": "application/json" };
    if (!serviceKey.startsWith("sb_secret_")) serverHeaders.authorization = `Bearer ${serviceKey}`;
    const rpc = await boundedJson(await fetcher(new URL("/rest/v1/rpc/np_erp_check_release", origins.internal), {
      method: "POST", redirect: "error", headers: serverHeaders, signal: AbortSignal.timeout(5000),
      body: JSON.stringify({ p_tenant_id: tenant, p_installation_id: installation, p_secret_hash: await hash(secret),
        p_nonce_hash: await hash(`${UPDATE_REQUEST_DOMAIN}:${tenant}:${installation}:${nonce}:${requestId}`),
        p_sent_at: new Date(Number(timestamp) * 1000).toISOString(), p_installed_version: (body as Record<string, unknown>).installed_version }),
    }));
    if (!record(rpc) || rpc.ok !== true) {
      const code = record(rpc) ? rpc.code : undefined;
      if (code === "unauthorized") throw new RecoveryContractError("unauthorized", 401);
      if (code === "replay") throw new RecoveryContractError("replay_rejected", 409);
      if (code === "rate_limited") throw new RecoveryContractError("rate_limited", 429);
      throw new RecoveryContractError("invalid_remote_response", 502);
    }
    if (typeof rpc.update_channel !== "string" || !CHANNELS.includes(rpc.update_channel) ||
        (origins.qa && rpc.update_channel !== "internal")) {
      throw new RecoveryContractError("invalid_remote_response", 502);
    }
    const manifest = validateUpdateManifest(rpc.release, rpc.update_channel,
      (body as Record<string, string>).installed_version, now);
    let download: Record<string, string> | null = null;
    if (manifest) {
      const path = `/storage/v1/object/sign/nexpoint-releases/windows/x64/${manifest.artifact_sha256}.exe`;
      const signed = await boundedJson(await fetcher(new URL(path, origins.internal), {
        method: "POST", redirect: "error", headers: serverHeaders, signal: AbortSignal.timeout(5000),
        body: JSON.stringify({ expiresIn: 300 }),
      }));
      if (!record(signed) || typeof signed.signedURL !== "string" || signed.signedURL.length > 8192) {
        throw new RecoveryContractError("invalid_remote_response", 502);
      }
      // Storage responds with /object/sign/...; reject arbitrary paths/origins.
      const prefix = path.slice("/storage/v1".length) + "?token=";
      if (!signed.signedURL.startsWith(prefix) || !/^[A-Za-z0-9_.-]{20,7000}$/.test(signed.signedURL.slice(prefix.length))) {
        throw new RecoveryContractError("invalid_remote_response", 502);
      }
      download = { url: new URL("/storage/v1" + signed.signedURL, origins.public).href,
        expires_at: new Date((now + 300) * 1000).toISOString() };
    }
    const encoded = JSON.stringify({ schema_version: 1, ok: true, request_id: requestId,
      update_channel: rpc.update_channel, release: manifest, download });
    headers.set("x-request-id", requestId);
    headers.set("x-nexpoint-timestamp", timestamp);
    headers.set("x-nexpoint-nonce", nonce);
    headers.set("x-nexpoint-signature", `v1=${await hmac(secret, [UPDATE_RESPONSE_DOMAIN, ...context, await hash(encoded)].join("\n"))}`);
    return new Response(encoded, { status: 200, headers });
  } catch (error) {
    const known = error instanceof RecoveryContractError ? error : new RecoveryContractError("service_unavailable", 503);
    if (known.status === 429) headers.set("retry-after", "60");
    return new Response(JSON.stringify({ schema_version: 1, ok: false, code: known.code }), { status: known.status, headers });
  }
}
