import { RecoveryContractError } from "./erp-admin-recovery-contract.ts";
import { record } from "./erp-update-contract.ts";
export { record };
export const ATTACHMENT_REQUEST_DOMAIN = "nexpoint-erp-support-attachments-v1";
export const ATTACHMENT_RESPONSE_DOMAIN = "nexpoint-erp-support-attachments-response-v1";
const UUID = /^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/;
const MAXIMUM = 1048576;
function invalid(): never { throw new RecoveryContractError("invalid_payload", 400); }
export async function readAttachmentBody(request: Request): Promise<Uint8Array> {
  const reader = request.body?.getReader();
  if (!reader) invalid();
  const chunks: Uint8Array[] = []; let size = 0;
  try { while (true) {
    const next = await reader.read(); if (next.done) break;
    size += next.value.length;
    if (size > 1405000) { await reader.cancel(); throw new RecoveryContractError("payload_too_large", 413); }
    chunks.push(next.value);
  } } finally { reader.releaseLock(); }
  const result = new Uint8Array(size); let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.length; }
  return result;
}
export function canonicalAttachmentBody(value: unknown, tenant: string, installation: string): string {
  if (!record(value) || Object.keys(value).sort().join(",") !==
      "action,attachment_id,content_type,data_base64,installation_id,name,schema_version,sha256,tenant_id,ticket_id" ||
      value.schema_version !== 1 || !["upload", "download", "list"].includes(String(value.action))) invalid();
  if (value.tenant_id !== tenant || value.installation_id !== installation) throw new RecoveryContractError("scope_mismatch", 403);
  if (typeof value.ticket_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,127}$/.test(value.ticket_id) ||
      typeof value.attachment_id !== "string" || (value.action === "list" ? value.attachment_id !== "" : !UUID.test(value.attachment_id))) invalid();
  for (const key of ["name", "content_type", "sha256", "data_base64"]) if (typeof value[key] !== "string") invalid();
  if (value.action === "upload") {
    validateImage(value.name as string, value.content_type as string, value.sha256 as string, value.data_base64 as string);
  } else if ([value.name, value.content_type, value.sha256, value.data_base64].some((x) => x !== "")) invalid();
  return JSON.stringify({ schema_version: 1, tenant_id: tenant, installation_id: installation, action: value.action,
    ticket_id: value.ticket_id, attachment_id: value.attachment_id, name: value.name, content_type: value.content_type,
    sha256: value.sha256, data_base64: value.data_base64 });
}
export function validateImage(name: string, type: string, sha256: string, base64: string): Uint8Array {
  if (!/^[A-Za-z0-9_][A-Za-z0-9_. ()-]{0,119}$/.test(name) || name.includes("..") || !/^[a-f0-9]{64}$/.test(sha256) ||
      base64.length > 1398104 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(base64)) invalid();
  let raw: string;
  try { raw = atob(base64); } catch { invalid(); }
  if (raw.length < 12 || raw.length > MAXIMUM || btoa(raw) !== base64) invalid();
  const bytes = Uint8Array.from(raw, (c) => c.charCodeAt(0));
  const prefix = Array.from(bytes.slice(0, 8), (x) => x.toString(16).padStart(2, "0")).join("");
  if (!((type === "image/png" && /\.png$/i.test(name) && prefix === "89504e470d0a1a0a") ||
      (type === "image/jpeg" && /\.jpe?g$/i.test(name) && prefix.startsWith("ffd8ff")) ||
      (type === "image/webp" && /\.webp$/i.test(name) && raw.slice(0, 4) === "RIFF" && raw.slice(8, 12) === "WEBP"))) invalid();
  const view = new DataView(bytes.buffer);
  if (name !== name.trim() || /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])\./i.test(name)) invalid();
  if (type === "image/png") {
    if (bytes.length < 45 || raw.slice(8, 16) !== "\x00\x00\x00\rIHDR" ||
        raw.slice(-12) !== "\x00\x00\x00\x00IEND\xaeB`\x82") invalid();
    const width = view.getUint32(16), height = view.getUint32(20);
    if (!width || !height || width > 16384 || height > 16384 || width * height > 40000000) invalid();
  } else if (type === "image/jpeg" && raw.slice(-2) !== "\xff\xd9") invalid();
  else if (type === "image/webp" && (bytes.length < 20 || !["VP8 ", "VP8L", "VP8X"].includes(raw.slice(12, 16)) ||
      view.getUint32(4, true) !== bytes.length - 8)) invalid();
  return bytes;
}
export async function validateAttachmentOutput(value: unknown, download: boolean): Promise<Record<string, unknown>[]> {
  if (!Array.isArray(value) || value.length > 3) throw new RecoveryContractError("invalid_remote_response", 502);
  return await Promise.all(value.map(async (entry: unknown) => {
    if (!record(entry) || typeof entry.id !== "string" || !UUID.test(entry.id) || typeof entry.created_at !== "string" ||
        !Number.isFinite(Date.parse(entry.created_at)) || !Number.isInteger(entry.size_bytes) || Number(entry.size_bytes) < 12 || Number(entry.size_bytes) > MAXIMUM ||
        typeof entry.name !== "string" || typeof entry.content_type !== "string" || typeof entry.sha256 !== "string" || typeof entry.data_base64 !== "string") {
      throw new RecoveryContractError("invalid_remote_response", 502);
    }
    if (download) {
      const bytes = validateImage(entry.name, entry.content_type, entry.sha256, entry.data_base64);
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", new Uint8Array(bytes))), (x) => x.toString(16).padStart(2, "0")).join("");
      if (digest !== entry.sha256 || bytes.length !== entry.size_bytes) throw new RecoveryContractError("invalid_remote_response", 502);
    } else if (entry.data_base64 !== "") throw new RecoveryContractError("invalid_remote_response", 502);
    return { id: entry.id, name: entry.name, content_type: entry.content_type, sha256: entry.sha256,
      size_bytes: entry.size_bytes, data_base64: entry.data_base64, created_at: entry.created_at };
  }));
}
