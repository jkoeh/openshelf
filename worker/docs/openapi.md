# Authoring routes with `@hono/zod-openapi`

Routes in this worker are defined with Zod schemas. The schemas drive **runtime validation, TypeScript inference, and the OpenAPI 3.1 spec** — there is no separate spec file to maintain. Edit a schema, and `/api/v1/openapi.json` updates on the next request.

## Pattern

Every route file looks like this:

```ts
import { createRoute, z } from "@hono/zod-openapi";
import { CACHE_IMMUTABLE } from "../constants";
import type { Env } from "../types";
import { ErrorSchema } from "../schemas/error";
import { SlugSchema } from "../schemas/params";
import { createOpenAPIApp } from "../utils/openapi-app";

const ParamsSchema = z.object({
  author: SlugSchema.openapi({ example: "franz-kafka" }),
  title:  SlugSchema.openapi({ example: "the-trial" }),
});

const ResponseSchema = z.object({ /* ... */ }).openapi("MyResponse");

const route = createRoute({
  method: "get",
  path: "/",
  tags: ["books"],
  summary: "Short imperative description",
  request: { params: ParamsSchema },
  responses: {
    200: { description: "Success", content: { "application/json": { schema: ResponseSchema } } },
    404: { description: "Not found", content: { "application/json": { schema: ErrorSchema } } },
  },
});

const app = createOpenAPIApp<{ Bindings: Env }>();

app.openapi(route, async (c) => {
  const { author, title } = c.req.valid("param");
  // ... fetch from R2, return c.json(body, 200, { "Cache-Control": CACHE_IMMUTABLE })
});

export default app;
```

## Rules

- **Use `createOpenAPIApp()`**, not `new OpenAPIHono()` directly. The factory installs a `defaultHook` that turns Zod parse failures into the standard `{ error: { code: "INVALID_PARAM", message } }` 400 body.
- **Every error you can return must be declared** in `responses`. If a route returns 404, list 404. If audio returns 416 on a bad Range, list 416. Otherwise the spec lies.
- **Inside `app.openapi(...)` handlers, return errors with `c.json({ error: { code, message } }, status)` directly.** Do not use `notFound()` / `badRequest()` from `utils/response.ts` — those return raw `Response` objects that bypass response-type checking. Helpers are still used by the global `onError`/`notFound` in `index.ts`.
- **Cache headers** go in the third arg to `c.json(body, status, headers)`. The schema doesn't model headers; that's intentional.
- **Binary endpoints** (audio, cover, epub) declare `content: { "audio/mp4": { schema: { type: "string", format: "binary" } } }` and return `new Response(obj.body, { status, headers })` directly. `app.openapi` does not validate non-JSON response bodies, so streaming and Range/206 handling work unchanged.
- **Mount path params** in `src/index.ts` (e.g. `/api/v1/books/:author/:title/chapters/:number`) must match the names in `ParamsSchema` exactly. The library converts `:author` to `{author}` for the spec.

## Shared schemas

- `schemas/error.ts` — `ErrorSchema` for `{ error: { code, message } }`. Use for all error responses.
- `schemas/params.ts` — `SlugSchema`, `ChapterNumberStringSchema`. Use for any path param that matches the same regex.

Add new shared schemas here when 2+ routes need them; route-local response schemas stay in the route file.

## Why Zod v3 (not v4)

`@hono/zod-openapi` v0.18.x requires Zod v3 — Zod v4 is a breaking change. The `package.json` pins `zod@^3.23.8`. When `@hono/zod-openapi` v1.x ships with Zod v4 support, upgrade both together.

## Verification after a change

```bash
npm run typecheck         # schemas + handler return types must agree
npm test                  # all 47 tests must pass
npm run dev:local         # boot
curl -s localhost:8787/api/v1/openapi.json | jq '.paths | keys'
open http://localhost:8787/api/v1/docs
```
