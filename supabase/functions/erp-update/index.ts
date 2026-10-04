import { handleErpUpdate } from "./handler.ts";
Deno.serve((request: Request) => handleErpUpdate(request));
