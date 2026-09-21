import { handleErpSync } from "./handler.ts";

Deno.serve((request: Request) => handleErpSync(request));
