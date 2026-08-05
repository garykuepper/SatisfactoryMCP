# The web wire rules

The rules every router in `interfaces/web/routers/` lives by. They are stated once, here;
routers point at this file instead of re-telling them.

1. **The function name is the operation_id.** FastAPI's default id is
   `{function_name}_{path}_{method}` and `frontend/src/api-schema.d.ts` is generated from
   it — renaming a handler churns the committed schema.
2. **Declaration order is wire order** for every `TypedDict` a `response_model` names: the
   keys are emitted in the order they are declared.
3. **A `response_model` FILTERS.** Keys the model does not declare are dropped from the
   response; a handler may build more than it sends. This is load-bearing — several
   endpoints deliberately send a subset of what the domain returns.
4. **Numeric types rewrite wire bytes.** Declaring `float` where the value is an int
   validates `15` into `15.0` on the wire, and vice versa fails validation. Match the real
   type of the value.
5. **A router never imports another router.** Shared shapes live in `serial.py` only when
   one function builds them for more than one router; two shapes that merely look alike stay
   separate. Enforced by `tests/test_architecture.py`.
6. **Regenerate, never hand-edit** `api-schema.d.ts`: throwaway server on a port in
   8920–8999, `openapi-typescript` against it, then `scripts/stamp-schema.mjs` (the
   `npm run typegen` recipe, with the URL pointed at the throwaway server).
