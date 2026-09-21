import {
  equalHex,
  hmacSha256Hex,
  parseAuthorization,
  parseInstallationId,
  parseNonce,
  parseSignature,
  parseSyncRequest,
  parseTimestamp,
  readBoundedBody,
  sha256Hex,
  signatureMessage,
  SyncContractError,
  validateAcks,
} from "../_shared/erp-sync-contract.ts";

export interface EnvironmentReader {
  get(name: string): string | undefined;
}

const runtimeEnvironment: EnvironmentReader = {
  get: (name) => Deno.env.get(name),
};

function headers(requestId: string): Headers {
  return new Headers({
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-request-id": requestId,
  });
}

function response(requestId: string, status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: headers(requestId) });
}

function requestId(request: Request): string {
  const supplied = request.headers.get("x-request-id") ?? "";
  return /^[0-9a-fA-F-]{36}$/.test(supplied) ? supplied.toLowerCase() : crypto.randomUUID();
}

function configuredEndpoint(reader: EnvironmentReader): { endpoint: URL; serviceKey: string } {
  const rawUrl = reader.get("SUPABASE_URL")?.trim();
  const serviceKey = reader.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!rawUrl || !serviceKey) throw new SyncContractError("service_unavailable", 503);
  try {
    const base = new URL(rawUrl);
    if (
      base.username || base.password || base.search || base.hash ||
      (base.protocol !== "https:" &&
        !(base.protocol === "http:" && ["127.0.0.1", "localhost"].includes(base.hostname)))
    ) throw new Error("invalid origin");
    return {
      endpoint: new URL("/rest/v1/rpc/erp_ingest_sync_batch", base),
      serviceKey,
    };
  } catch {
    throw new SyncContractError("service_unavailable", 503);
  }
}

function remoteError(value: unknown): SyncContractError {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return new SyncContractError("remote_rejected", 502);
  }
  const code = (value as Record<string, unknown>).code;
  switch (code) {
    case "rate_limited":
      return new SyncContractError("rate_limited", 429);
    case "replay":
      return new SyncContractError("replay_rejected", 409);
    case "idempotency_conflict":
      return new SyncContractError("idempotency_conflict", 409);
    case "unauthorized":
    case "tenant_invalid":
    case "installation_invalid":
      return new SyncContractError("unauthorized", 401);
    case "invalid_payload":
      return new SyncContractError("invalid_payload", 400);
    default:
      return new SyncContractError("remote_rejected", 502);
  }
}

export async function handleErpSync(
  request: Request,
  reader: EnvironmentReader = runtimeEnvironment,
  fetcher: typeof fetch = fetch,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<Response> {
  const resolvedRequestId = requestId(request);
  try {
    if (request.method !== "POST" || request.headers.has("origin")) {
      throw new SyncContractError("method_not_allowed", 405);
    }
    if (
      request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() !==
        "application/json"
    ) throw new SyncContractError("unsupported_media_type", 415);

    const installationId = parseInstallationId(
      request.headers.get("x-nexpoint-installation-id"),
    );
    const timestampText = request.headers.get("x-nexpoint-timestamp");
    const timestamp = parseTimestamp(timestampText, nowSeconds);
    const nonce = parseNonce(request.headers.get("x-nexpoint-nonce"));
    const providedSignature = parseSignature(request.headers.get("x-nexpoint-signature"));
    const secret = parseAuthorization(request.headers.get("authorization"));
    const body = await readBoundedBody(request);
    const bodyHash = await sha256Hex(body);
    const expectedSignature = await hmacSha256Hex(
      secret,
      signatureMessage(installationId, timestampText!, nonce, bodyHash),
    );
    if (!equalHex(expectedSignature, providedSignature)) {
      throw new SyncContractError("unauthorized", 401);
    }

    let decoded: unknown;
    try {
      decoded = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body));
    } catch {
      throw new SyncContractError("invalid_payload", 400);
    }
    const syncRequest = parseSyncRequest(decoded, installationId);
    const { endpoint, serviceKey } = configuredEndpoint(reader);
    const secretHash = await sha256Hex(secret);
    const nonceHash = await sha256Hex(`${installationId}:${nonce}`);
    let remoteResponse: Response;
    try {
      remoteResponse = await fetcher(endpoint, {
        method: "POST",
        headers: {
          apikey: serviceKey,
          authorization: `Bearer ${serviceKey}`,
          "content-type": "application/json",
          "x-request-id": resolvedRequestId,
        },
        body: JSON.stringify({
          p_installation_id: installationId,
          p_secret_hash: secretHash,
          p_nonce_hash: nonceHash,
          p_sent_at: new Date(timestamp * 1000).toISOString(),
          p_envelopes: syncRequest.items,
        }),
        signal: AbortSignal.timeout(5_000),
      });
    } catch {
      throw new SyncContractError("service_unavailable", 503);
    }
    let remoteBody: unknown;
    try {
      remoteBody = await remoteResponse.json();
    } catch {
      throw new SyncContractError("invalid_remote_ack", 502);
    }
    if (!remoteResponse.ok) throw remoteError(remoteBody);
    if (
      typeof remoteBody === "object" && remoteBody !== null && !Array.isArray(remoteBody) &&
      (remoteBody as Record<string, unknown>).ok === false
    ) throw remoteError(remoteBody);
    const acks = validateAcks(remoteBody, syncRequest);
    return response(resolvedRequestId, 200, { ok: true, acks });
  } catch (error) {
    const known = error instanceof SyncContractError
      ? error
      : new SyncContractError("internal_error", 500);
    const result = response(resolvedRequestId, known.status, { ok: false, code: known.code });
    if (known.status === 429) result.headers.set("retry-after", "30");
    return result;
  }
}
