import { handleErpSupportAttachments } from "./handler.ts";
Deno.serve((request: Request) => handleErpSupportAttachments(request));
