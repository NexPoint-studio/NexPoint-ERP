import { RecoveryContractError } from "./erp-admin-recovery-contract.ts";
export { record } from "./erp-update-contract.ts";
import { record } from "./erp-update-contract.ts";
export const SUPPORT_REQUEST_DOMAIN = "nexpoint-erp-support-v1";
export const SUPPORT_RESPONSE_DOMAIN = "nexpoint-erp-support-response-v1";
export function canonicalSupportBody(value: unknown, tenant: string, installation: string): string {
  const fields = "action,body,installation_id,operation_id,schema_version,status,tenant_id,ticket_id";
  if (!record(value) || Object.keys(value).sort().join(",") !== fields || value.schema_version !== 1 ||
      !["list", "detail", "history", "reply", "status"].includes(String(value.action))) {
    throw new RecoveryContractError("invalid_payload", 400);
  }
  if (value.tenant_id !== tenant || value.installation_id !== installation) throw new RecoveryContractError("scope_mismatch", 403);
  const mutation = value.action === "reply" || value.action === "status";
  if (typeof value.ticket_id !== "string" || (value.action === "list" ? value.ticket_id !== "" :
      !/^[A-Za-z0-9][A-Za-z0-9_.:@/+\-]{0,127}$/.test(value.ticket_id)) ||
      typeof value.operation_id !== "string" || (mutation ?
      !/^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.operation_id) : value.operation_id !== "") ||
      typeof value.body !== "string" || (value.action === "reply" ?
      value.body.trim().length < 1 || value.body.length > 4000 || /[\x00-\x08\x0b\x0c\x0e-\x1f]/.test(value.body) :
      ["list", "history"].includes(String(value.action)) ? !/^(0|[1-9][0-9]{0,5})?$/.test(value.body) || Number(value.body) > 100000 : value.body !== "") ||
      (value.action === "status" ? !["open", "resolved", "closed"].includes(String(value.status)) : value.status !== "")) {
    throw new RecoveryContractError("invalid_payload", 400);
  }
  return JSON.stringify({ schema_version: 1, tenant_id: tenant, installation_id: installation,
    action: value.action, ticket_id: value.ticket_id, operation_id: value.operation_id, body: value.body, status: value.status });
}
