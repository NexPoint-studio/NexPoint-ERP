import { RecoveryContractError } from "./erp-admin-recovery-contract.ts";

export const UPDATE_REQUEST_DOMAIN = "nexpoint-erp-update-v1";
export const UPDATE_RESPONSE_DOMAIN = "nexpoint-erp-update-response-v1";
const VERSION = /^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$/;
const HASH = /^[a-f0-9]{64}$/;
export const CHANNELS = ["stable", "beta", "internal"];

export function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function canonicalUpdateBody(value: unknown, tenant: string, installation: string): string {
  if (!record(value) || Object.keys(value).sort().join(",") !== "installation_id,installed_version,schema_version,tenant_id" ||
      value.schema_version !== 1 || typeof value.installed_version !== "string" || !VERSION.test(value.installed_version)) {
    throw new RecoveryContractError("invalid_payload", 400);
  }
  if (value.tenant_id !== tenant || value.installation_id !== installation) {
    throw new RecoveryContractError("scope_mismatch", 403);
  }
  return JSON.stringify({ schema_version: 1, tenant_id: tenant, installation_id: installation,
    installed_version: value.installed_version });
}

function parts(version: string): number[] { return version.split(".").map(Number); }
function compare(left: string, right: string): number {
  const a = parts(left), b = parts(right);
  for (let i = 0; i < 3; i++) { if (a[i] !== b[i]) return a[i] - b[i]; }
  return 0;
}

export function validateUpdateManifest(value: unknown, channel: string, installed: string, now: number): Record<string, unknown> | null {
  if (value === null) return null;
  const fields = ["schema_version", "release_id", "sequence", "version", "update_channel", "platform", "architecture",
    "build_commit", "artifact_sha256", "artifact_size", "release_notes", "minimum_supported_version", "mandatory",
    "published_at", "code_signing", "signer_thumbprint"].sort();
  if (!record(value) || Object.keys(value).sort().join(",") !== fields.join(",") || value.schema_version !== 1 ||
      typeof value.release_id !== "string" || !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.release_id) ||
      !Number.isSafeInteger(value.sequence) || Number(value.sequence) < 1 ||
      typeof value.version !== "string" || !VERSION.test(value.version) ||
      typeof value.minimum_supported_version !== "string" || !VERSION.test(value.minimum_supported_version) ||
      compare(value.version, installed) <= 0 || compare(value.minimum_supported_version, value.version) > 0 ||
      value.update_channel !== channel || !CHANNELS.includes(channel) || value.platform !== "windows" || value.architecture !== "x64" ||
      typeof value.build_commit !== "string" || !/^[a-f0-9]{40}$/.test(value.build_commit) ||
      typeof value.artifact_sha256 !== "string" || !HASH.test(value.artifact_sha256) ||
      !Number.isSafeInteger(value.artifact_size) || Number(value.artifact_size) < 1 || Number(value.artifact_size) > 536870912 ||
      typeof value.mandatory !== "boolean" || typeof value.release_notes !== "string" ||
      value.release_notes.length < 1 || value.release_notes.length > 4000 || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value.release_notes) ||
      typeof value.published_at !== "string" || value.published_at.length > 40 ||
      !/(Z|[+-][0-9]{2}:[0-9]{2})$/.test(value.published_at) || !Number.isFinite(Date.parse(value.published_at)) ||
      Date.parse(value.published_at) > (now + 300) * 1000 ||
      !((value.code_signing === "CODE_SIGNING_PENDING" && value.signer_thumbprint === null) ||
        (value.code_signing === "AUTHENTICODE" && typeof value.signer_thumbprint === "string" &&
          /^([A-F0-9]{40}|[A-F0-9]{64})$/.test(value.signer_thumbprint)))) {
    throw new RecoveryContractError("invalid_remote_response", 502);
  }
  return value;
}
