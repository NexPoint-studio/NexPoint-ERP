import { handleErpHandoff } from "./handler.ts";

Deno.serve((request: Request) => handleErpHandoff(request));
