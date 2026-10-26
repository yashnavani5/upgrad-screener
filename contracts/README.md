# contracts/

Generated JSON Schema for every `v_*` view row. This folder is the hand-off point between the Python
backend and the TypeScript frontend. **Do not edit the `.json` files by hand.**

## Flow

```
Alembic migration (CREATE OR REPLACE VIEW v_*)
        │  columns must match
        ▼
src/screener/contracts.py      Pydantic v2 models, one per view (VIEW_CONTRACTS)
        │  uv run screener export-schema
        ▼
contracts/<view>.json          JSON Schema (draft 2020-12), e.g. v_shortlist.json
        │  npm run gen:types   (in /web, json-schema-to-typescript)
        ▼
web/lib/contracts.ts           generated TS types (do not edit)
        │
        ▼
web/lib/schemas.ts             hand-written zod schemas typed as z.ZodType<Row>, so drift fails `tsc`
```

| View | Pydantic model | Schema file |
|---|---|---|
| `v_shortlist` | `ShortlistRow` | `v_shortlist.json` |
| `v_company_detail` | `CompanyDetailRow` | `v_company_detail.json` |
| `v_left_out` | `LeftOutRow` | `v_left_out.json` |
| `v_long_list` | `LongListRow` | `v_long_list.json` |
| `v_runs` | `RunRow` | `v_runs.json` |
| `v_funnel` | `FunnelRow` | `v_funnel.json` |

## When a view changes

1. Write the Alembic migration (`CREATE OR REPLACE VIEW …` + `GRANT SELECT … TO web_ro`).
2. Update the model in `src/screener/contracts.py`.
3. Regenerate:
   ```powershell
   uv run screener export-schema
   cd web; npm run gen:types
   ```
   ```bash
   uv run screener export-schema
   (cd web && npm run gen:types)
   ```
4. Update `web/lib/schemas.ts` until `npm run typecheck` passes.
5. Run `uv run pytest -q` (includes the view ↔ contract column-parity test).
6. Commit the migration, `contracts.py`, `contracts/*.json` and `web/lib/contracts.ts` **in the same PR**.

CI (later) fails if `export-schema` or `gen:types` produces a diff against the committed files.
