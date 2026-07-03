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
   run, check whether a review JSON (e.g. `semantic_view_review.json` or
   whatever `--review-file` produced last time) already exists in the working
   directory — a plain existence check (e.g. Glob) is enough; don't read the
   file's contents just to decide which question to ask. If one exists:
   - Ask the user whether they want to **resume** (edit it further /
     re-render as-is via `--from-review`, per step 5) or **start over** with
     a fresh classify-mode run — don't assume either silently.
   - If resuming, run the gap-check from step 3.5 once (a review JSON from
     before that step existed may still have comment gaps), fill any gaps
     found, then skip to step 5 (no new scope/inventory/connection needed —
     this is exactly the case where the user re-invoked the skill in a new
     session instead of just continuing the old conversation). Read the
     file's contents only once resuming is confirmed and you actually need
     to inspect or edit it.
   - If starting over, continue to step 1 as normal; a fresh classify run
     will overwrite the existing review JSON, so mention that before running
     it. If the user wants to check whether it has hand-edits worth keeping
     first, that's the point to read it — not before they've chosen.

1. **Determine scope.** Before asking the user anything, check for an existing
   scope file in the working directory (e.g. `scope.json`, or anything
   matching `*scope*.json` besides the `templates/` examples) via Glob — a
   plain existence check is enough, don't read it yet. If one is found, tell
   the user it exists and ask whether to use it as-is, edit it first, or
   ignore it and specify scope fresh; don't assume silently. Only if no scope
   file exists and the user hasn't already given you a natural-language
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

3.5. **Fill in missing descriptions — compulsory, fill-gaps-only.** Cortex
   Analyst/Agent reads a semantic view's `COMMENT`s as interpretation
   instructions, not just human documentation, so every `TABLES`/`FACTS`/
   `DIMENSIONS`/`METRICS` entry must end up with a meaningful description —
   never leave a gap unfilled, and never overwrite a real Snowflake comment
   that already exists.

   - **Extract gaps** without reading the full review JSON into the
     conversation (it "can run to hundreds or thousands of lines," same
     concern as step 4 below). Run a small `python -c` snippet via Bash,
     following the same load/inspect/print pattern used in step 5, that
     loads the review JSON and prints a compact list of only the items
     missing a comment: tables with no `"comment"`; fact/dimension columns
     (from `roles`) whose entry in that table's `"columns"` list has no
     `"comment"`; metrics with no `"comment"`. For each, include table name,
     column name (if any), data type, role, the table's own comment if
     present, and sibling columns' names/roles/comments on the same table
     for grounding — this keeps the output proportional to the number of
     gaps, not the schema size.
   - If the list is empty, there's nothing to do — continue to step 4.
   - **Compose descriptions.** For each gap, write one concise sentence
     using the table/column name, data type, role, and sibling context —
     the review JSON carries no profiled sample values (only
     `sf_detector.py`'s name/type/comment/role fields), so this is
     necessarily heuristic/best-effort, same spirit as the role
     classification itself.
   - **Write descriptions back** with a second targeted `python -c`
     mutation (load/mutate/dump, not Read+Edit):
     - Table gap → set that table dict's `"comment"`.
     - Fact/dimension gap → find the matching entry in that table's
       `"columns"` list by `column_name`, set its `"comment"`. Do **not**
       edit the `facts`/`dimensions` list entries directly — `load_review()`
       rebuilds them fresh from `columns` on every render, so a direct edit
       there is silently discarded.
     - Metric gap → also set the matching entry's `"comment"` directly in
       the top-level `"metrics"` list, by `(table, name)`. This is required
       even after fixing the underlying fact's column comment:
       `load_review()` reuses an existing metric entry from the JSON
       verbatim once one exists for that `(table, name)` key, so it will
       **not** re-derive the comment from the fact automatically.
   - **Re-render** via `--from-review` (same command as step 5) so the
     filled-in descriptions flow through `_comment_clause()` into the SQL,
     then continue to step 4.

4. **Present the result — summary only, not full file contents.** The
   `python` command's own console output already includes a summary table
   (tables with their inferred keys/facts/dimensions, plus inferred
   relationships) and a **"Suggested Review Points"** table; relay that
   summary and the SQL/review-JSON file paths to the user. Don't `Read` the
   full SQL or review JSON into the conversation at this step — on a
   real-sized schema either file can run to hundreds or thousands of lines,
   and the summary already contains everything needed to decide what to fix.
   Only read a file in full if the user explicitly asks to see its contents.
   The same hints are written inline into the review JSON too (as a
   top-level `review_hints` list and a `"hint"` field on the flagged column
   itself), so the file is self-explanatory if the user opens it themselves.
   Point the user at the hints first, since they call out exactly the
   columns most likely to be misclassified (non-primary "key" columns that
   are either an unmatched foreign key or a measure the >0.95-uniqueness
   heuristic mistook for a key). Explain the classification overall is
   heuristic (name patterns + cardinality ratios), not guaranteed correct,
   and that **the review JSON — not the SQL — is the file to correct**: edit
   a column's `"role"` (`key`/`fact`/`dimension`), its `"primary"` flag, or
   the `relationships`/`metrics` lists. Also mention that any table/fact/
   dimension/metric missing a Snowflake comment already had a description
   generated for it in step 3.5, so the presented SQL has no comment gaps.

5. **Iterate via the review file, capped at 2 rounds.** If the user wants a
   column reclassified or a relationship/metric changed, either the user
   edits the review JSON directly, or — the default when you're applying the
   edit on their behalf — you run a small `python -c` snippet via Bash that
   loads the JSON, mutates just the relevant field(s), and writes it back
   (`json.load`/mutate/`json.dump`), rather than using `Read`+`Edit`. This
   keeps the (potentially large) review file out of the conversation, since
   the edit is a targeted, mechanical field change, not something that needs
   visual inspection. Reserve `Read`+`Edit` for cases where the user
   specifically wants to see a section of the file before changing it. Then
   re-render deterministically with no reprofiling and no chat-based SQL
   editing:
   ```
   python src/semantic_view_builder.py --from-review <review.json> --output semantic_view.sql
   ```
   This needs no Snowflake connection and no inventory/scope flags — it's a
   pure function of the review file, and it also writes the reconciled
   `facts`/`dimensions`/`metrics`/`review_hints` back into the review JSON
   itself (`roles` stays the source of truth; these derived fields are
   refreshed so the file never disagrees with the SQL it just produced).
   Allow at most **2** such edit-and-rerender cycles. If a third round of
   corrections is requested, tell the user plainly that further automated
   cycles aren't likely to converge faster than a direct fix, and offer to
   make the final change directly in the rendered SQL instead.

   After each re-render, run the step 3.5 gap-check once more before
   presenting the updated SQL — an edit can promote a column to fact/
   dimension (or add a metric) that doesn't have a description yet, and this
   must stay filled. This gap-check-and-fill doesn't count as one of the 2
   rounds; it's an always-on completeness pass, not a user-requested
   iteration.

   If the user wants different tables in scope entirely (not just a
   reclassification), that's not a review-file edit — re-run classify mode
   with a narrower/wider `--tables`/`--description`/`--scope-file` instead.

6. **Before running the SQL against Snowflake**, explicitly flag that it's a
   best-effort draft: `CREATE SEMANTIC VIEW` syntax should be checked against
   current Snowflake documentation, since the exact clause grammar may drift
   between Snowflake releases. Only execute it against the account if the user
   explicitly confirms they want that. Two specific failure modes are worth
   naming up front so they aren't mistaken for a bug in this tool:
   - A syntax error specifically on the `RELATIONSHIPS` clause usually means
     the Snowflake account/version doesn't support that clause at all, not a
     malformed statement — suggest dropping that block and retrying.
   - A "Dimension entity must be related to and have an equal or lower level
     of granularity" error means two joined tables' relationship isn't at a
     clean 1:many grain — this tool's role/relationship inference doesn't
     check grain compatibility. For a multi-table draft, suggest validating
     incrementally against Snowflake (one table's dimensions, then its
     metrics, then add the next table) rather than running the whole
     statement at once, so a grain problem is easy to isolate.
