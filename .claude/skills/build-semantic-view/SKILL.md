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
   run, check whether any per-view review JSONs already exist — Glob for
   `semantic_views_output/*/semantic_view_review.json` (or wherever
   `--output-dir` produced them last time) — a plain existence check is
   enough; don't read file contents just to decide which question to ask. If
   any exist:
   - List the view folders found, and ask the user whether they want to
     **resume** (edit one further / re-render as-is via `--from-review`, per
     step 5) or **start over** with a fresh classify-mode run — don't assume
     either silently.
   - If resuming, run the gap-check from step 3.5 once per view being
     resumed (a review JSON from before that step existed may still have
     comment gaps), fill any gaps found, then skip to step 5 (no new
     scope/inventory/connection needed — this is exactly the case where the
     user re-invoked the skill in a new session instead of just continuing
     the old conversation). Read a file's contents only once resuming is
     confirmed and you actually need to inspect or edit it.
   - If starting over, continue to step 1 as normal; a fresh classify run
     overwrites the view folders named in the scope file, so mention that
     before running it. If the user wants to check whether an existing
     review has hand-edits worth keeping first, that's the point to read it
     — not before they've chosen.

1. **Determine scope.** Before asking the user anything, check for an existing
   scope file in the working directory (e.g. `scope.json`, or anything
   matching `*scope*.json` besides the `templates/` examples) via Glob — a
   plain existence check is enough, don't read it yet. If one is found, tell
   the user it exists and ask whether to use it as-is, edit it first, or
   ignore it and specify scope fresh; don't assume silently. Only if no scope
   file exists and the user hasn't already given you a natural-language
   description of the analysis goal ("build a semantic view for order
   analysis") or an explicit table list (`db.schema.table,...`), ask for one.

   `src/semantic_view_builder.py` can build **multiple semantic views in one
   run**: a `--scope-file <path>` JSON file holds a `"views"` list, each
   entry giving a `view_name` plus either a `description` or a `tables` list
   (see `templates/semantic_view_scope.example.json`, which shows one of
   each) — mix natural-language and explicit-table entries freely in the
   same file. A `tables`-based entry can add optional `database`/`schema`
   fields so its table names don't need to be fully qualified (any table
   without a `.` in it is resolved against those defaults; a table string
   with a `.` is used as-is, so mixing schemas within one view still works).
   For a single quick one-off without a file, `--description`/
   `--tables` plus `--view-name` on the command line still works. A scope
   file is preferable whenever the user wants to save/edit/reuse the scope,
   or is building more than one view.

2. **Confirm an inventory exists.** The tool reads `tables.json`/`views.json`
   from an `sf_detector.py` output directory (default
   `./snowflake_inventory_output`). If it's missing or stale for the relevant
   database/schema, tell the user to run `/scan-snowflake-inventory` first
   (or invoke it yourself if they confirm) before continuing.

3. **Run the builder in classify mode:**
   ```
   python src/semantic_view_builder.py --inventory-dir <sf_detector_output_dir> \
     --scope-file <scope.json>  # or --description "<goal>" / --tables db.schema.table,... --view-name <NAME> \
     --output-dir semantic_views_output \
     --account ... --user ... --auth-method ...
   ```
   Reuses the same `--account/--user/--role/--warehouse/--auth-method/--private-key-path/--env-file`
   flags as `sf_detector.py` — if the user already has a `.env`/`.secrets` file
   configured for that tool, it works unchanged here.

   One command builds **every** view listed in the scope file (or the single
   ad hoc view from `--description`/`--tables`). Each view gets its own
   `<output-dir>/<view_name>/` folder containing the draft SQL
   (`semantic_view.sql`) and an editable review JSON
   (`semantic_view_review.json`) — a full serialization of every column's
   classification, including ones that were dropped from the draft SQL (e.g.
   columns misclassified as `key`), so nothing is silently hidden from the
   user. Tables shared by more than one view are profiled only once.

   This step runs live queries against Snowflake (exact `COUNT(DISTINCT)` +
   small ordered `SELECT DISTINCT` samples per column) to classify columns as
   keys/facts/dimensions, so it needs a real, working connection.

3.5. **Fill in missing descriptions — compulsory, fill-gaps-only, per view.**
   Cortex Analyst/Agent reads a semantic view's `COMMENT`s as interpretation
   instructions, not just human documentation, so every `TABLES`/`FACTS`/
   `DIMENSIONS`/`METRICS` entry must end up with a meaningful description —
   never leave a gap unfilled, and never overwrite a real Snowflake comment
   that already exists. When classify mode built multiple views, repeat this
   step once per view's own `semantic_view_review.json`.

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
   `python` command's own console output already includes, per view, a
   summary table (tables with their inferred keys/facts/dimensions, plus
   inferred relationships) and a **"Suggested Review Points"** table; relay
   that summary and each view's SQL/review-JSON folder path to the user.
   Don't `Read` the
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

   **Stop here and wait for the user.** Never edit `roles`/`relationships`/
   `metrics` on your own initiative just because `review_hints` flagged
   something — flagging is informational, not an instruction to act. Do not
   silently "fix" a flagged column and only mention it afterward. If you
   have an opinion on a flagged item (e.g. "this looks like a measure, not
   a key"), say so and ask whether to apply that change — don't apply it
   first and ask after. Proceed to step 5 only once the user has told you
   what to change, said the draft looks fine as-is, or said they'll edit
   the review JSON themselves and let you know when to re-render.

5. **Iterate via the review file, capped at 2 rounds.** This step only
   starts once the user has given explicit direction on what to change
   (a specific column/relationship/metric fix, or "yes, apply what you
   suggested") — not proactively from step 4's hints alone. Either the user
   edits the review JSON directly, or — when the user has confirmed you
   should apply the edit on their behalf — you run a small `python -c`
   snippet via Bash that loads the JSON, mutates just the relevant field(s)
   the user approved, and writes it back
   (`json.load`/mutate/`json.dump`), rather than using `Read`+`Edit`. This
   keeps the (potentially large) review file out of the conversation, since
   the edit is a targeted, mechanical field change, not something that needs
   visual inspection. Reserve `Read`+`Edit` for cases where the user
   specifically wants to see a section of the file before changing it. Then
   re-render deterministically with no reprofiling and no chat-based SQL
   editing:
   ```
   python src/semantic_view_builder.py --from-review semantic_views_output/<view_name>/semantic_view_review.json
   ```
   This needs no Snowflake connection and no inventory/scope flags — it's a
   pure function of the review file. SQL is written as `semantic_view.sql`
   alongside the review file (same folder), and the reconciled
   `facts`/`dimensions`/`metrics`/`review_hints` are written back into the
   review JSON itself (`roles` stays the source of truth; these derived
   fields are refreshed so the file never disagrees with the SQL it just
   produced). When multiple views were built, re-render each changed view
   separately — this only ever operates on one review file at a time.
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
