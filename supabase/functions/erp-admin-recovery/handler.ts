import {
  canonicalRecoveryBody,
  equalRecoveryHex,
  parseInstallationId,
  parseRecoveryAuthorization,
  parseRecoveryRequest,
  parseRecoverySignature,
  parseRecoveryTimestamp,
  parseRecoveryUuid,
  parseTenantId,
  readRecoveryBody,
  RecoveryContractError,
  recoveryHmacSha256Hex,
  recoveryRequestSignatureMessage,
  recoveryResponseSignatureMessage,
  recoverySha256Hex,
  validateRecoveryRpcAuthorization,
} from "../_shared/erp-admin-recovery-contract.ts";

export interface RecoveryEnvironmentReader {
  get(name: string): string | undefined;
}

const runtimeEnvironment: RecoveryEnvironmentReader = {
  get: (name) => Deno.env.get(name),
};

function responseHeaders(requestId: string): Headers {
  return new Headers({
    "cache-control": "no-store",
    "content-type": "application/json; charset=utf-8",
    "referrer-policy": "no-referrer",
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "x-request-id": requestId,
  });
}

function plainResponse(requestId: string, status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), { status, headers: responseHeaders(requestId) });
}

async function signedResponse(
  requestId: string,
  status: number,
  body: Record<string, unknown>,
  secret: string,
  tenantId: string,
  installationId: string,
  timestamp: string,
  nonce: string,
): Promise<Response> {
  const encoded = JSON.stringify(body);
  const signature = await recoveryHmacSha256Hex(
    secret,
    recoveryResponseSignatureMessage(
      tenantId,
      installationId,
      timestamp,
      nonce,
      requestId,
      await recoverySha256Hex(encoded),
    ),
  );
  const headers = responseHeaders(requestId);
  headers.set("x-nexpoint-timestamp", timestamp);
  headers.set("x-nexpoint-nonce", nonce);
  headers.set("x-nexpoint-signature", `v1=${signature}`);
  return new Response(encoded, { status, headers });
}

function configuredEndpoint(
  reader: RecoveryEnvironmentReader,
): { endpoint: URL; serviceKey: string } {
  const rawUrl = reader.get("SUPABASE_URL")?.trim();
  const serviceKey = reader.get("SUPABASE_SERVICE_ROLE_KEY")?.trim();
  if (!rawUrl || !serviceKey) throw new RecoveryContractError("service_unavailable", 503);
  try {
    const base = new URL(rawUrl);
    if (
      base.username || base.password || base.search || base.hash ||
      (base.protocol !== "https:" &&
        !(base.protocol === "http:" && ["127.0.0.1", "localhost"].includes(base.hostname)))
    ) throw new Error("invalid origin");
    return {
      endpoint: new URL("/rest/v1/rpc/np_erp_access_reset_authorization", base),
      serviceKey,
    };
  } catch {
    throw new RecoveryContractError("service_unavailable", 503);
  }
}

function remoteError(value: unknown): RecoveryContractError {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return new RecoveryContractError("remote_rejected", 502);
  }
  switch ((value as Record<string, unknown>).code) {
    case "rate_limited":
      return new RecoveryContractError("rate_limited", 429);
    case "replay":
      return new RecoveryContractError("replay_rejected", 409);
    case "ambiguous_context":
      return new RecoveryContractError("ambiguous_context", 409);
    case "authorization_unavailable":
      return new RecoveryContractError("authorization_unavailable", 409);
    case "unauthorized":
      return new RecoveryContractError("unauthorized", 401);
    case "invalid_payload":
      return new RecoveryContractError("invalid_payload", 400);
    default:
      return new RecoveryContractError("remote_rejected", 502);
  }
}

export async function handleErpAdminRecovery(
  request: Request,
  reader: RecoveryEnvironmentReader = runtimeEnvironment,
  fetcher: typeof fetch = fetch,
  nowSeconds = Math.floor(Date.now() / 1000),
): Promise<Response> {
  let requestId: string = crypto.randomUUID();
  let secret: string | undefined;
  let tenantId: string | undefined;
  let installationId: string | undefined;
  let timestampText: string | undefined;
  let nonce: string | undefined;

  try {
    if (request.method !== "POST" || request.headers.has("origin")) {
      throw new RecoveryContractError("method_not_allowed", 405);
    }
    if (
      request.headers.get("content-type")?.split(";", 1)[0]?.trim().toLowerCase() !==
        "application/json"
    ) throw new RecoveryContractError("unsupported_media_type", 415);

    requestId = parseRecoveryUuid(request.headers.get("x-request-id"));
    tenantId = parseTenantId(request.headers.get("x-nexpoint-tenant-id"));
    installationId = parseInstallationId(request.headers.get("x-nexpoint-installation-id"));
    timestampText = request.headers.get("x-nexpoint-timestamp") ?? undefined;
    const timestamp = parseRecoveryTimestamp(timestampText ?? null, nowSeconds);
    nonce = parseRecoveryUuid(request.headers.get("x-nexpoint-nonce"));
    const suppliedSignature = parseRecoverySignature(
      request.headers.get("x-nexpoint-signature"),
    );
    secret = parseRecoveryAuthorization(request.headers.get("authorization"));

    const rawBody = await readRecoveryBody(request);
    let decoded: unknown;
    let rawText: string;
    try {
      rawText = new TextDecoder("utf-8", { fatal: true }).decode(rawBody);
      decoded = JSON.parse(rawText);
    } catch {
      throw new RecoveryContractError("invalid_payload", 400);
    }
    const recoveryRequest = parseRecoveryRequest(decoded, tenantId, installationId);
    const canonicalBody = canonicalRecoveryBody(recoveryRequest);
    if (rawText !== canonicalBody) throw new RecoveryContractError("invalid_payload", 400);
    const canonicalHash = await recoverySha256Hex(canonicalBody);
    const expectedSignature = await recoveryHmacSha256Hex(
      secret,
      recoveryRequestSignatureMessage(
        tenantId,
        installationId,
        timestampText!,
        nonce,
        requestId,
        canonicalHash,
      ),
    );
    if (!equalRecoveryHex(expectedSignature, suppliedSignature)) {
      throw new RecoveryContractError("unauthorized", 401);
    }

    const { endpoint, serviceKey } = configuredEndpoint(reader);
    let remoteResponse: Response;
    try {
      remoteResponse = await fetcher(endpoint, {
        method: "POST",
        headers: {
          apikey: serviceKey,
          authorization: `Bearer ${serviceKey}`,
          "content-type": "application/json",
          "x-request-id": requestId,
        },
        body: JSON.stringify({
          p_tenant_id: tenantId,
          p_installation_id: installationId,
          p_secret_hash: await recoverySha256Hex(secret),
          p_nonce_hash: await recoverySha256Hex(
            `${tenantId}:${installationId}:${nonce}:${requestId}`,
          ),
          p_sent_at: new Date(timestamp * 1000).toISOString(),
          p_action: recoveryRequest.action,
          p_requester_ref: recoveryRequest.requester_ref,
          p_authorization_id: recoveryRequest.authorization_id ?? null,
        }),
        signal: AbortSignal.timeout(5_000),
      });
    } catch {
      throw new RecoveryContractError("service_unavailable", 503);
    }

    let remoteBody: unknown;
    try {
      remoteBody = await remoteResponse.json();
    } catch {
      throw new RecoveryContractError("invalid_remote_response", 502);
    }
    if (!remoteResponse.ok) throw remoteError(remoteBody);
    if (
      typeof remoteBody !== "object" || remoteBody === null || Array.isArray(remoteBody) ||
      (remoteBody as Record<string, unknown>).ok !== true ||
      (remoteBody as Record<string, unknown>).action !== recoveryRequest.action
    ) throw remoteError(remoteBody);

    const authorization = validateRecoveryRpcAuthorization(
      (remoteBody as Record<string, unknown>).authorization,
      tenantId,
      installationId,
    );
    if (recoveryRequest.action === "consume" && authorization === null) {
      throw new RecoveryContractError("invalid_remote_response", 502);
    }
    return await signedResponse(
      requestId,
      200,
      { ok: true, request_id: requestId, authorization },
      secret,
      tenantId,
      installationId,
      timestampText!,
      nonce,
    );
  } catch (error) {
    const known = error instanceof RecoveryContractError
      ? error
      : new RecoveryContractError("internal_error", 500);
    const body = { schema_version: 1, ok: false, code: known.code };
    if (secret && tenantId && installationId && timestampText && nonce) {
      const result = await signedResponse(
        requestId,
        known.status,
        body,
        secret,
        tenantId,
        installationId,
        timestampText,
        nonce,
      );
      if (known.status === 429) result.headers.set("retry-after", "30");
      return result;
    }
    return plainResponse(requestId, known.status, body);
  }
}
