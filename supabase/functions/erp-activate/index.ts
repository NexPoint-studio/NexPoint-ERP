import { handleErpActivation } from "./handler.ts";

Deno.serve((request: Request) => handleErpActivation(request));
