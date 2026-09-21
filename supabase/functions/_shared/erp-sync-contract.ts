const encoder = new TextEncoder();

export const SYNC_SIGNATURE_DOMAIN = "nexpoint-erp-sync-v1";
export const MAX_SYNC_BODY_BYTES = 256 * 1024;
export const MAX_SYNC_BATCH_ITEMS = 25;
export const MAX_CLOCK_SKEW_SECONDS = 300;

export interface SyncEnvelopeInput {
  event_type: string;
  aggregate_type: string;
  aggregate_id: string;
  payload: Record<string, unknown>;
  schema_version: 1;
  idempotency_key: string;
}

export interface SyncRequestInput {
  schema_version: 1;
  items: SyncEnvelopeInput[];
}

export interface SyncAckOutput {
  idempotency_key: string;
  remote_id: string;
  schema_version: number;
  duplicate: boolean;
}

const EVENT_TYPES = new Set([
  "heartbeat",
  "health",
  "diagnostic_event",
  "risk",
  "incident",
  "support_ticket",
]);
const SAFE_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const INSTALLATION_IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$/;
const NONCE = /^[0-9a-f]{64}$/;
const SIGNATURE = /^v1=([0-9a-f]{64})$/;
const BEARER = /^Bearer ([A-Za-z0-9_-]{43,256})$/;
const SENSITIVE_KEYS = [
  "password",
  "passwd",
  "passphrase",
  "senha",
  "secret",
  "segredo",
  "credential",
  "privatekey",
  "apikey",
  "token",
  "authorization",
  "cookie",
  "servicerole",
  "hmac",
];
const TOP_LEVEL_KEYS = new Set(["schema_version", "items"]);
const ENVELOPE_KEYS = new Set([
  "event_type",
  "aggregate_type",
  "aggregate_id",
  "payload",
  "schema_version",
  "idempotency_key",
]);

export class SyncContractError extends Error {
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

function normalizedKey(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]/g, "");
}

function productionMarker(value: unknown): boolean {
  return typeof value === "string" && ["prod", "production"].includes(value.toLowerCase());
}

function rejectSensitiveTree(value: unknown, depth = 0, visited = new WeakSet<object>()): void {
  if (depth > 10) throw new SyncContractError("invalid_payload", 400);
  if (Array.isArray(value)) {
    if (visited.has(value)) throw new SyncContractError("invalid_payload", 400);
    visited.add(value);
    for (const item of value) rejectSensitiveTree(item, depth + 1, visited);
    return;
  }
  if (!record(value)) return;
  if (visited.has(value)) throw new SyncContractError("invalid_payload", 400);
  visited.add(value);
  for (const [key, child] of Object.entries(value)) {
    const normalized = normalizedKey(key);
    if (SENSITIVE_KEYS.some((marker) => normalized.includes(marker))) {
      throw new SyncContractError("sensitive_payload_rejected", 400);
    }
    rejectSensitiveTree(child, depth + 1, visited);
  }
}

export function parseAuthorization(value: string | null): string {
  const match = value?.match(BEARER);
  if (!match) throw new SyncContractError("unauthorized", 401);
  return match[1];
}

export function parseInstallationId(value: string | null): string {
  if (!value || !INSTALLATION_IDENTIFIER.test(value)) {
    throw new SyncContractError("unauthorized", 401);
  }
  return value;
}

export function parseTimestamp(value: string | null, nowSeconds: number): number {
  if (!value || !/^\d{10}$/.test(value)) {
    throw new SyncContractError("unauthorized", 401);
  }
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || Math.abs(nowSeconds - parsed) > MAX_CLOCK_SKEW_SECONDS) {
    throw new SyncContractError("request_expired", 401);
  }
  return parsed;
}

export function parseNonce(value: string | null): string {
  if (!value || !NONCE.test(value)) throw new SyncContractError("unauthorized", 401);
  return value;
}

export function parseSignature(value: string | null): string {
  const match = value?.match(SIGNATURE);
  if (!match) throw new SyncContractError("unauthorized", 401);
  return match[1];
}

export function parseSyncRequest(value: unknown, installationId: string): SyncRequestInput {
  if (!record(value) || value.schema_version !== 1 || !Array.isArray(value.items)) {
    throw new SyncContractError("invalid_payload", 400);
  }
  if (Object.keys(value).some((key) => !TOP_LEVEL_KEYS.has(key))) {
    throw new SyncContractError("invalid_payload", 400);
  }
  if (value.items.length < 1 || value.items.length > MAX_SYNC_BATCH_ITEMS) {
    throw new SyncContractError("invalid_batch_size", 400);
  }
  const items: SyncEnvelopeInput[] = [];
  const keys = new Set<string>();
  for (const candidate of value.items) {
    if (!record(candidate) || !record(candidate.payload)) {
      throw new SyncContractError("invalid_payload", 400);
    }
    if (Object.keys(candidate).some((key) => !ENVELOPE_KEYS.has(key))) {
      throw new SyncContractError("invalid_payload", 400);
    }
    const eventType = candidate.event_type;
    const aggregateType = candidate.aggregate_type;
    const aggregateId = candidate.aggregate_id;
    const idempotencyKey = candidate.idempotency_key;
    const schemaVersion = candidate.schema_version;
    if (
      typeof eventType !== "string" || !EVENT_TYPES.has(eventType) ||
      typeof aggregateType !== "string" || !SAFE_IDENTIFIER.test(aggregateType) ||
      typeof aggregateId !== "string" || !SAFE_IDENTIFIER.test(aggregateId) ||
      typeof idempotencyKey !== "string" || !SAFE_IDENTIFIER.test(idempotencyKey) ||
      schemaVersion !== 1 ||
      ("environment" in candidate.payload && !productionMarker(candidate.payload.environment)) ||
      ("channel" in candidate.payload && !productionMarker(candidate.payload.channel))
    ) {
      throw new SyncContractError("invalid_payload", 400);
    }
    if (keys.has(idempotencyKey)) throw new SyncContractError("duplicate_batch_key", 400);
    keys.add(idempotencyKey);
    if (candidate.payload.installation_id !== installationId) {
      throw new SyncContractError("installation_scope_mismatch", 403);
    }
    const tenantId = candidate.payload.tenant_id;
    if (typeof tenantId !== "string" || !SAFE_IDENTIFIER.test(tenantId)) {
      throw new SyncContractError("tenant_scope_missing", 403);
    }
    rejectSensitiveTree(candidate.payload);
    if (encoder.encode(JSON.stringify(candidate.payload)).byteLength > 65_536) {
      throw new SyncContractError("payload_too_large", 413);
    }
    items.push({
      event_type: eventType,
      aggregate_type: aggregateType,
      aggregate_id: aggregateId,
      payload: candidate.payload,
      schema_version: 1,
      idempotency_key: idempotencyKey,
    });
  }
  return { schema_version: 1, items };
}

export async function readBoundedBody(request: Request): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) throw new SyncContractError("invalid_payload", 400);
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_SYNC_BODY_BYTES) {
        await reader.cancel();
        throw new SyncContractError("payload_too_large", 413);
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

export async function sha256Hex(value: Uint8Array | string): Promise<string> {
  const bytes = typeof value === "string" ? encoder.encode(value) : value;
  const ownedBuffer = Uint8Array.from(bytes).buffer;
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", ownedBuffer)));
}

export function signatureMessage(
  installationId: string,
  timestamp: string,
  nonce: string,
  bodyHash: string,
): string {
  return `${SYNC_SIGNATURE_DOMAIN}\n${installationId}\n${timestamp}\n${nonce}\n${bodyHash}`;
}

export async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(message))));
}

export function equalHex(expected: string, provided: string): boolean {
  if (expected.length !== provided.length) return false;
  let mismatch = 0;
  for (let index = 0; index < expected.length; index++) {
    mismatch |= expected.charCodeAt(index) ^ provided.charCodeAt(index);
  }
  return mismatch === 0;
}

export function validateAcks(value: unknown, request: SyncRequestInput): SyncAckOutput[] {
  if (!record(value) || value.ok !== true || !Array.isArray(value.acks)) {
    throw new SyncContractError("invalid_remote_ack", 502);
  }
  if (value.acks.length !== request.items.length) {
    throw new SyncContractError("invalid_remote_ack", 502);
  }
  const expected = new Map(request.items.map((item) => [item.idempotency_key, item]));
  const observed = new Set<string>();
  return value.acks.map((candidate) => {
    if (!record(candidate)) throw new SyncContractError("invalid_remote_ack", 502);
    const key = candidate.idempotency_key;
    const remoteId = candidate.remote_id;
    const schemaVersion = candidate.schema_version;
    const duplicate = candidate.duplicate;
    const source = typeof key === "string" ? expected.get(key) : undefined;
    if (
      !source || observed.has(key as string) ||
      typeof remoteId !== "string" || !SAFE_IDENTIFIER.test(remoteId) ||
      schemaVersion !== source.schema_version || typeof duplicate !== "boolean"
    ) {
      throw new SyncContractError("invalid_remote_ack", 502);
    }
    observed.add(key as string);
    return {
      idempotency_key: key as string,
      remote_id: remoteId,
      schema_version: schemaVersion as number,
      duplicate,
    };
  });
}
