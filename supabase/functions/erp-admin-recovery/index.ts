import { handleErpAdminRecovery } from "./handler.ts";

Deno.serve((request: Request) => handleErpAdminRecovery(request));
