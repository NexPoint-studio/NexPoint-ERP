import { equalRecoveryHex as equalHex, parseInstallationId, parseRecoveryAuthorization,
  parseRecoverySignature, parseRecoveryTimestamp, parseRecoveryUuid, parseTenantId,
  readRecoveryBody, RecoveryContractError, recoveryHmacSha256Hex as hmac,
  recoverySha256Hex as hash } from "../_shared/erp-admin-recovery-contract.ts";
import { canonicalHandoffBody, HANDOFF_REQUEST_DOMAIN, HANDOFF_RESPONSE_DOMAIN,
  validateHandoffReceipt } from "../_shared/erp-handoff-contract.ts";

export interface HandoffEnvironment { get(name: string): string | undefined; }
const environment: HandoffEnvironment = { get: (name) => Deno.env.get(name) };

function errorResponse(error: unknown): Response {
  const known = error instanceof RecoveryContractError ? error :
    new RecoveryContractError("service_unavailable", 503);
  return new Response(JSON.stringify({ schema_version: 1, ok: false, code: known.code }), {
    status: known.status, headers: { "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer" },
  });
}

async function boundedRemoteJson(response: Response): Promise<unknown> {
  if (!response.ok || response.headers.get("content-type")?.split(";", 1)[0] !== "application/json") {
    throw new RecoveryContractError("service_unavailable", 503);
  }
  const stream = response.body?.getReader();
  if (!stream) throw new RecoveryContractError("invalid_remote_response", 502);
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { value, done } = await stream.read();
      if (done) break;
      size += value.byteLength;
      if (size > 8192) {
        await stream.cancel();
        throw new RecoveryContractError("invalid_remote_response", 502);
      }
      chunks.push(value);
    }
  } finally { stream.releaseLock(); }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); }
  catch { throw new RecoveryContractError("invalid_remote_response", 502); }
}

export async function handleErpHandoff(request: Request, reader: HandoffEnvironment = environment,
                                       fetcher: typeof fetch = fetch,
                                       now = Math.floor(Date.now() / 1000)): Promise<Response> {
  try {
    if (request.method !== "POST" || request.headers.has("origin")) {
      throw new RecoveryContractError("method_not_allowed", 405);
    }
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
    if (text !== canonicalHandoffBody(body, tenant, installation)) {
      throw new RecoveryContractError("invalid_payload", 400);
    }
    const operation = (body as Record<string, string>).operation_id;
    const action = (body as Record<string, string>).action;
    const context = [tenant, installation, timestamp, nonce, requestId];
    if (!equalHex(await hmac(secret, [HANDOFF_REQUEST_DOMAIN, ...context, await hash(text)].join("\n")),
                  signature)) throw new RecoveryContractError("unauthorized", 401);
    const rawBase = reader.get("SUPABASE_URL"), serviceKey = reader.get("SUPABASE_SERVICE_ROLE_KEY");
    if (!rawBase || !serviceKey) throw new RecoveryContractError("service_unavailable", 503);
    const base = new URL(rawBase);
    if (base.username || base.password || base.search || base.hash ||
        (base.pathname !== "/" && base.pathname !== "") ||
        (base.protocol !== "https:" && !(base.protocol === "http:" &&
          ["127.0.0.1", "localhost"].includes(base.hostname)))) {
      throw new RecoveryContractError("service_unavailable", 503);
    }
    const serverHeaders: Record<string, string> = { apikey: serviceKey,
      "content-type": "application/json" };
    if (!serviceKey.startsWith("sb_secret_")) serverHeaders.authorization = `Bearer ${serviceKey}`;
    let remote: Response;
    try {
      remote = await fetcher(new URL("/rest/v1/rpc/np_erp_handoff_receipt", base), {
        method: "POST", redirect: "error", headers: serverHeaders, signal: AbortSignal.timeout(5000),
        body: JSON.stringify({ p_presented_tenant_key: tenant, p_installation_key: installation,
          p_operation_id: operation, p_secret_hash: await hash(secret),
          p_nonce_hash: await hash(`${HANDOFF_REQUEST_DOMAIN}:${tenant}:${installation}:${nonce}:${requestId}`),
          p_sent_at: new Date(Number(timestamp) * 1000).toISOString(),
          p_confirm: action === "confirm" }),
      });
    } catch { throw new RecoveryContractError("service_unavailable", 503); }
    const rpc = await boundedRemoteJson(remote);
    if (typeof rpc === "object" && rpc !== null && "ok" in rpc && rpc.ok === false) {
      const code = "code" in rpc ? rpc.code : "";
      if (code === "replay") throw new RecoveryContractError("replay_rejected", 409);
      throw new RecoveryContractError("unauthorized", 401);
    }
    const receipt = validateHandoffReceipt(rpc, operation, installation);
    if (action === "confirm" && receipt.state !== "confirmed") {
      throw new RecoveryContractError("invalid_remote_response", 502);
    }
    const encoded = JSON.stringify({ schema_version: 1, ok: true, request_id: requestId,
      receipt });
    const headers = new Headers({ "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store", "x-content-type-options": "nosniff", "referrer-policy": "no-referrer",
      "x-request-id": requestId, "x-nexpoint-timestamp": timestamp, "x-nexpoint-nonce": nonce });
    headers.set("x-nexpoint-signature", "v1=" + await hmac(secret,
      [HANDOFF_RESPONSE_DOMAIN, ...context, await hash(encoded)].join("\n")));
    return new Response(encoded, { status: 200, headers });
  } catch (error) { return errorResponse(error); }
}
