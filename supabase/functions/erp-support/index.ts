import { handleErpSupport } from "./handler.ts";
Deno.serve((request: Request) => handleErpSupport(request));
