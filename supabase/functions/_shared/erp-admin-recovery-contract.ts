const encoder = new TextEncoder();

export const RECOVERY_REQUEST_DOMAIN = "nexpoint-erp-admin-recovery-v1";
export const RECOVERY_RESPONSE_DOMAIN = "nexpoint-erp-admin-recovery-response-v1";
export const MAX_RECOVERY_BODY_BYTES = 16 * 1024;
export const MAX_RECOVERY_CLOCK_SKEW_SECONDS = 60;

const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,159}$/;
const INSTALLATION_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{7,119}$/;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const SIGNATURE = /^v1=([0-9a-f]{64})$/;
const BEARER = /^Bearer ([A-Za-z0-9_-]{43,256})$/;
const REQUEST_KEYS = new Set([
  "schema_version",
  "action",
  "tenant_id",
  "installation_id",
  "requester_ref",
  "authorization_id",
]);
const AUTHORIZATION_KEYS = new Set([
  "authorization_id",
  "tenant_id",
  "installation_id",
  "ticket_id",
  "authorized_by",
  "created_at",
  "expires_at",
  "consumed_at",
]);

export type RecoveryAction = "status" | "consume";

export interface RecoveryRequestInput {
  schema_version: 1;
  action: RecoveryAction;
  tenant_id: string;
  installation_id: string;
  requester_ref: string;
  authorization_id?: string;
}

export interface RecoveryAuthorizationOutput {
  authorization_id: string;
  tenant_id: string;
  installation_id: string;
  ticket_id: string;
  authorized_by: string;
  created_at: string;
  expires_at: string;
  consumed_at: string | null;
}

export class RecoveryContractError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status: number) {
    super(code);
    this.code = code;
    this.status = status;
  }
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function identifier(value: unknown, maximum: number): value is string {
  return typeof value === "string" && value.length <= maximum && IDENTIFIER.test(value);
}

function isoTimestamp(value: unknown): value is string {
  if (typeof value !== "string" || value.length > 40) return false;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) && /(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value);
}

export function parseRecoveryAuthorization(value: string | null): string {
  const match = value?.match(BEARER);
  if (!match) throw new RecoveryContractError("unauthorized", 401);
  return match[1];
}

export function parseTenantId(value: string | null): string {
  if (!identifier(value, 80)) throw new RecoveryContractError("unauthorized", 401);
  return value;
}

export function parseInstallationId(value: string | null): string {
  if (!value || !INSTALLATION_IDENTIFIER.test(value)) {
    throw new RecoveryContractError("unauthorized", 401);
  }
  return value;
}

export function parseRecoveryUuid(value: string | null): string {
  if (!value || !UUID.test(value)) throw new RecoveryContractError("unauthorized", 401);
  return value;
}

export function parseRecoveryTimestamp(value: string | null, nowSeconds: number): number {
  if (!value || !/^\d{10}$/.test(value)) {
    throw new RecoveryContractError("unauthorized", 401);
  }
  const parsed = Number(value);
  if (
    !Number.isSafeInteger(parsed) ||
    Math.abs(nowSeconds - parsed) > MAX_RECOVERY_CLOCK_SKEW_SECONDS
  ) throw new RecoveryContractError("request_expired", 401);
  return parsed;
}

export function parseRecoverySignature(value: string | null): string {
  const match = value?.match(SIGNATURE);
  if (!match) throw new RecoveryContractError("unauthorized", 401);
  return match[1];
}

export function parseRecoveryRequest(
  value: unknown,
  tenantId: string,
  installationId: string,
): RecoveryRequestInput {
  if (!record(value) || Object.keys(value).some((key) => !REQUEST_KEYS.has(key))) {
    throw new RecoveryContractError("invalid_payload", 400);
  }
  if (
    value.schema_version !== 1 ||
    (value.action !== "status" && value.action !== "consume") ||
    !identifier(value.requester_ref, 160)
  ) throw new RecoveryContractError("invalid_payload", 400);
  if (value.tenant_id !== tenantId || value.installation_id !== installationId) {
    throw new RecoveryContractError("scope_mismatch", 403);
  }
  if (
    (value.action === "status" && value.authorization_id !== undefined) ||
    (value.action === "consume" &&
      (typeof value.authorization_id !== "string" || !UUID.test(value.authorization_id)))
  ) throw new RecoveryContractError("invalid_payload", 400);
  const parsed: RecoveryRequestInput = {
    schema_version: 1,
    action: value.action,
    tenant_id: tenantId,
    installation_id: installationId,
    requester_ref: value.requester_ref,
  };
  if (value.action === "consume") parsed.authorization_id = value.authorization_id as string;
  return parsed;
}

export function canonicalRecoveryBody(value: RecoveryRequestInput): string {
  const canonical: Record<string, unknown> = {
    schema_version: 1,
    action: value.action,
    tenant_id: value.tenant_id,
    installation_id: value.installation_id,
    requester_ref: value.requester_ref,
  };
  if (value.authorization_id !== undefined) {
    canonical.authorization_id = value.authorization_id;
  }
  return JSON.stringify(canonical);
}

export async function readRecoveryBody(request: Request): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) throw new RecoveryContractError("invalid_payload", 400);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_RECOVERY_BODY_BYTES) {
        await reader.cancel();
        throw new RecoveryContractError("payload_too_large", 413);
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const body = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return body;
}

export function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function recoverySha256Hex(value: Uint8Array | string): Promise<string> {
  const bytes = typeof value === "string" ? encoder.encode(value) : value;
  const ownedBuffer = Uint8Array.from(bytes).buffer;
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", ownedBuffer)));
}

export async function recoveryHmacSha256Hex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message))));
}

export function equalRecoveryHex(expected: string, supplied: string): boolean {
  if (expected.length !== supplied.length) return false;
  let mismatch = 0;
  for (let index = 0; index < expected.length; index++) {
    mismatch |= expected.charCodeAt(index) ^ supplied.charCodeAt(index);
  }
  return mismatch === 0;
}

export function recoveryRequestSignatureMessage(
  tenantId: string,
  installationId: string,
  timestamp: string,
  nonce: string,
  requestId: string,
  canonicalBodyHash: string,
): string {
  return [
    RECOVERY_REQUEST_DOMAIN,
    tenantId,
    installationId,
    timestamp,
    nonce,
    requestId,
    canonicalBodyHash,
  ].join("\n");
}

export function recoveryResponseSignatureMessage(
  tenantId: string,
  installationId: string,
  timestamp: string,
  nonce: string,
  requestId: string,
  bodyHash: string,
): string {
  return [
    RECOVERY_RESPONSE_DOMAIN,
    tenantId,
    installationId,
    timestamp,
    nonce,
    requestId,
    bodyHash,
  ].join("\n");
}

export function validateRecoveryRpcAuthorization(
  value: unknown,
  tenantId: string,
  installationId: string,
): RecoveryAuthorizationOutput | null {
  if (value === null) return null;
  if (!record(value) || Object.keys(value).some((key) => !AUTHORIZATION_KEYS.has(key))) {
    throw new RecoveryContractError("invalid_remote_response", 502);
  }
  if (
    !UUID.test(String(value.authorization_id ?? "")) ||
    value.tenant_id !== tenantId ||
    value.installation_id !== installationId ||
    !identifier(value.ticket_id, 128) ||
    !UUID.test(String(value.authorized_by ?? "")) ||
    !isoTimestamp(value.created_at) ||
    !isoTimestamp(value.expires_at) ||
    (value.consumed_at !== null && !isoTimestamp(value.consumed_at))
  ) throw new RecoveryContractError("invalid_remote_response", 502);
  return {
    authorization_id: value.authorization_id as string,
    tenant_id: tenantId,
    installation_id: installationId,
    ticket_id: value.ticket_id as string,
    authorized_by: value.authorized_by as string,
    created_at: value.created_at as string,
    expires_at: value.expires_at as string,
    consumed_at: value.consumed_at as string | null,
  };
}
