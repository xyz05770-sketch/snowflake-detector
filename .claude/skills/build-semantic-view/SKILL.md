---
name: build-semantic-view
description: Draft a Snowflake native CREATE SEMANTIC VIEW statement from an sf_detector inventory, inferring keys/facts/dimensions/relationships. Use when the user wants to build, draft, or generate a Snowflake semantic view / Cortex Analyst semantic model.
---

# Build Semantic View

Helps the user draft a Snowflake `CREATE SEMANTIC VIEW` statement using the
inventory produced by `sf_detector.py` plus live cardinality/sample profiling
of the shortlisted tables.

## Flow

0. **Check for an in-progress review first.** Before assuming this is a fresh
   run, look for an existing review JSON (e.g. `semantic_view_review.json` or
   whatever `--review-file` produced last time) in the working directory. If
   one exists:
   - Ask the user whether they want to **resume** (edit it further /
     re-render as-is via `--from-review`, per step 5) or **start over** with
     a fresh classify-mode run — don't assume either silently.
   - If resuming, skip straight to step 5 (no new scope/inventory/connection
     needed — this is exactly the case where the user re-invoked the skill
     in a new session instead of just continuing the old conversation).
   - If starting over, continue to step 1 as normal; a fresh classify run
     will overwrite the existing review JSON, so mention that before running
     it if the file looks like it has hand-edits worth keeping (e.g. its
     `"role"` values differ from what a plain heuristic run would likely
     produce — when in doubt, ask rather than silently clobbering it).

1. **Determine scope.** If the user hasn't already given you a natural-language
   description of the analysis goal ("build a semantic view for order
   analysis") or an explicit table list (`db.schema.table,...`), ask for one.
   Either is fine — `src/semantic_view_builder.py` supports both, inline via
   `--description`/`--tables` or via a `--scope-file <path>` JSON file (see
   `templates/semantic_view_scope.description.example.json` and
   `templates/semantic_view_scope.tables.example.json`). A scope file is
   preferable when the user wants to save/edit/reuse the scope.

2. **Confirm an inventory exists.** The tool reads `tables.json`/`views.json`
   from an `sf_detector.py` output directory (default
   `./snowflake_inventory_output`). If it's missing or stale for the relevant
   database/schema, tell the user to run `/scan-snowflake-inventory` first
   (or invoke it yourself if they confirm) before continuing.

3. **Run the builder in classify mode:**
   ```
   python src/semantic_view_builder.py --inventory-dir <sf_detector_output_dir> \
     --scope-file <scope.json>  # or --description "<goal>" / --tables db.schema.table,... \
     --view-name <NAME> --output semantic_view.sql \
     --account ... --user ... --auth-method ...
   ```
   Reuses the same `--account/--user/--role/--warehouse/--auth-method/--private-key-path/--env-file`
   flags as `sf_detector.py` — if the user already has a `.env`/`.secrets` file
   configured for that tool, it works unchanged here.

   This step runs live queries against Snowflake (exact `COUNT(DISTINCT)` +
   small ordered `SELECT DISTINCT` samples per column) to classify columns as
   keys/facts/dimensions, so it needs a real, working connection. It writes
   **two** files: the draft SQL (`--output`) and an editable review JSON
   (`--review-file`, default `<output-stem>_review.json`) — the review JSON
   is a full serialization of every column's classification, including ones
   that were dropped from the draft SQL (e.g. columns misclassified as
   `key`), so nothing is silently hidden from the user.

4. **Present the result:** show the generated SQL file, the review JSON's
   path, and the console summary (tables with their inferred
   keys/facts/dimensions, plus inferred relationships). The summary also
   includes a **"Suggested Review Points"** table, and the same hints are
   written inline into the review JSON (as a top-level `review_hints` list
   and a `"hint"` field on the flagged column itself) — point the user at
   these first, since they call out exactly the columns most likely to be
   misclassified (non-primary "key" columns that are either an unmatched
   foreign key or a measure the >0.95-uniqueness heuristic mistook for a
   key). Explain the classification overall is heuristic (name patterns +
   cardinality ratios), not guaranteed correct, and that **the review JSON —
   not the SQL — is the file to correct**: edit a column's `"role"`
   (`key`/`fact`/`dimension`), its `"primary"` flag, or the
   `relationships`/`metrics` lists.

5. **Iterate via the review file, capped at 2 rounds.** If the user wants a
   column reclassified or a relationship/metric changed, apply the edit to
   the review JSON (either the user edits it directly, or you edit it on
   their behalf from their description of the fix), then re-render
   deterministically with no reprofiling and no chat-based SQL editing:
   ```
   python src/semantic_view_builder.py --from-review <review.json> --output semantic_view.sql
   ```
   This needs no Snowflake connection and no inventory/scope flags — it's a
   pure function of the review file. Allow at most **2** such
   edit-and-rerender cycles. If a third round of corrections is requested,
   tell the user plainly that further automated cycles aren't likely to
   converge faster than a direct fix, and offer to make the final change
   directly in the rendered SQL instead.

   If the user wants different tables in scope entirely (not just a
   reclassification), that's not a review-file edit — re-run classify mode
   with a narrower/wider `--tables`/`--description`/`--scope-file` instead.

6. **Before running the SQL against Snowflake**, explicitly flag that it's a
   best-effort draft: `CREATE SEMANTIC VIEW` syntax should be checked against
   current Snowflake documentation, since the exact clause grammar may drift
   between Snowflake releases. Only execute it against the account if the user
   explicitly confirms they want that.
