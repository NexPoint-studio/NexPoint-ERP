import { RecoveryContractError } from "./erp-admin-recovery-contract.ts";

export const HANDOFF_REQUEST_DOMAIN = "nexpoint-erp-handoff-v1";
export const HANDOFF_RESPONSE_DOMAIN = "nexpoint-erp-handoff-response-v1";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const HASH = /^[0-9a-f]{64}$/;
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,159}$/;

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function canonicalHandoffBody(value: unknown, tenant: string, installation: string): string {
  if (!record(value) || Object.keys(value).sort().join(",") !==
      "action,installation_id,operation_id,schema_version,tenant_id" ||
      value.schema_version !== 1 || !["receipt", "confirm"].includes(String(value.action)) ||
      typeof value.operation_id !== "string" || !UUID.test(value.operation_id)) {
    throw new RecoveryContractError("invalid_payload", 400);
  }
  if (value.tenant_id !== tenant || value.installation_id !== installation) {
    throw new RecoveryContractError("scope_mismatch", 403);
  }
  return JSON.stringify({ schema_version: 1, action: value.action, tenant_id: tenant,
    installation_id: installation, operation_id: value.operation_id });
}

export function validateHandoffReceipt(value: unknown, operation: string,
                                       installation: string): Record<string, unknown> {
  const keys = ["ok", "schema_version", "operation_id", "installation_id", "source_tenant_id",
    "target_tenant_id", "target_tenant_kind", "company_name", "installation_label",
    "backup_sha256", "local_preflight_sha256", "state"].sort().join(",");
  if (!record(value) || Object.keys(value).sort().join(",") !== keys || value.ok !== true ||
      value.schema_version !== 1 || value.operation_id !== operation ||
      value.installation_id !== installation || value.target_tenant_kind !== "customer" ||
      !["cloud_committed", "confirmed"].includes(String(value.state)) ||
      typeof value.source_tenant_id !== "string" || !IDENTIFIER.test(value.source_tenant_id) ||
      typeof value.target_tenant_id !== "string" || !IDENTIFIER.test(value.target_tenant_id) ||
      value.source_tenant_id === value.target_tenant_id ||
      typeof value.company_name !== "string" || value.company_name.length < 1 ||
      value.company_name.length > 180 || /[\x00-\x1f\x7f]/.test(value.company_name) ||
      typeof value.installation_label !== "string" || value.installation_label.length < 1 ||
      value.installation_label.length > 160 || /[\x00-\x1f\x7f]/.test(value.installation_label) ||
      typeof value.backup_sha256 !== "string" || !HASH.test(value.backup_sha256) ||
      typeof value.local_preflight_sha256 !== "string" || !HASH.test(value.local_preflight_sha256)) {
    throw new RecoveryContractError("invalid_remote_response", 502);
  }
  return value;
}
