# Hair & Scalp Assistant — production overlay patch

This ZIP is an **overlay patch** for the original `current-healthcare-weightlose` repository. It is not a complete application archive. Apply the files at the same relative paths in a branch/staging environment first. Do not apply it on top of the experimental LangGraph archive.

## Scope

- Replaces the weight-loss onboarding flow with the manager's hair-loss questions. Question wording and option labels are preserved; formatting is limited to WhatsApp-readable line breaks/bullets. For safety, the sexual-activity question is only asked when the user is 18 or older; `Prefer not to say` is accepted. That answer is stored in a dedicated column but is excluded from normal LLM context and summaries.
- Adds nullable hair profile columns and `hair_onboarding_complete` to the existing `users` table using migration `0009`. Existing rows, consent timestamps, profile fields and subscription/payment/message records remain intact. Existing users are asked to complete the hair profile because the new completion flag is intentionally false on migration.
- Adds a separate `hair_care_plans` table in migration `0010`. The legacy `diet_plans` table and historical records are not renamed, truncated or deleted.
- Uses Gemini structured output plus deterministic validation to generate one daily **general self-care routine**, with a safety note. It is not a diagnosis or treatment plan; it must not recommend medicines, doses, supplements, tests, expensive water treatments, restrictive diets, or guarantee regrowth. Sudden/patchy loss, eyebrow/eyelash loss, or inflamed/painful scalp symptoms trigger clinician-referral guidance.
- Preserves the existing daily scheduler pattern at **06:00 Asia/Kolkata**, active-subscription gate, stored plan history, check-in buttons, plan-revision flow, Redis/ARQ worker, WhatsApp integration and payment/subscription model. Routine generation is supported for the existing product age range of 12–75. Sexual activity is not used to personalize the routine.
- Adds persisted delivery/check-in states, unique constraints per user/date and user/day, bounded retries for definite failures, and a no-blind-resend policy for ambiguous WhatsApp delivery outcomes. The admin user-detail page now shows hair-plan delivery/check-in statuses and errors while keeping the sensitive sexual-activity answer out of the admin query.
- Old daily diet-plan scheduling and queued job handling are disabled/no-op. Historical diet-plan records remain visible as legacy history. No LangChain/LangGraph or new runtime dependency is added.
- Adds paraphrased AAD/NHS hair-loss guidance as authoritative RAG material. User-specific answers remain in PostgreSQL; they are not added to Chroma.
- Retains the existing high-risk medical-history gate for legacy profiles that already have a recorded high-risk condition. Such users can still ask general hair/scalp questions, but automated personalized routines are withheld. Empty legacy medical fields are shown in admin as “No prior answer saved,” not as a claim that the user answered “No.”
- The bundled source references and paraphrases must be reviewed against current source guidance and source/license terms by the product owner before public release; the package is not a substitute for clinical/legal review.

## WhatsApp 24-hour rule

The app sends free-form WhatsApp text/interactive messages only while Meta's 24-hour customer-service window is open. At 06:00 IST, an eligible user's routine is generated and stored. If the window is closed, the plan remains in `awaiting_window` and is sent when the user next sends a message. The app does **not** bypass the rule or pretend an unapproved template exists. To push routines to users who have not messaged within 24 hours, obtain/confirm an approved Meta template and its exact parameters before adding that feature.

After a routine is sent, the user is prompted to answer Done / Not done / Skip. If there is no check-in within 24 hours of routine delivery, the previous check-in is recorded as `no_response` (without inventing a user answer) and the next daily routine can proceed. An ambiguous send (`unknown`) is not automatically resent, to avoid duplicate delivery. Admins can inspect the state in the user detail page; provider delivery reconciliation may be required.

## Production rollout checklist

1. Take and verify a PostgreSQL backup. Test the backup restore process if possible.
2. Apply this overlay to a feature branch and deploy to staging first. Keep all existing files not included in this ZIP.
3. Run `alembic upgrade head`. The migration chain should end at `0010`. On Render, the repository's existing pre-deploy command runs `alembic upgrade head`; confirm the logs show both `0009` and `0010` completed.
4. Rebuild the knowledge base so the hair guidance is in the deployed Chroma store. The bundled Docker build invokes `python scripts/build_knowledge_base.py --replace`. For a persistent/self-hosted Chroma volume, back it up and run the builder from the new image against that same configured volume (for example `docker compose run --rm app python scripts/build_knowledge_base.py --replace`). The builder includes existing sources as well as the new hair source; do not delete the volume manually.
5. For the cutover, pause/scale down the old ARQ worker before enabling the new release so its old 06:00 diet-plan cron cannot send another diet plan during a rolling deploy. Deploy the web app and new ARQ worker together, then confirm the worker heartbeat and daily-hair-plan cron logs. Webhook jobs can wait in Redis during the brief worker pause.
6. Smoke-test on a test number: consent; each onboarding question; family relation appears only after `Yes`; `No`/`Not sure` skips relation; a `Prefer not to say` response completes onboarding; an active subscribed user aged 12–75 gets one routine; an under-18 user is not asked sexual-activity; no plan is created for users outside the routine age range; no routine is sent outside the WhatsApp window; a routine is not duplicated for a user/date; the check-in buttons record once; saved-plan retrieval and plan revision work; an expired subscription receives no new plan.
7. Inspect an admin user detail page to confirm it shows the new plan content, delivery/check-in statuses, attempt counts and last errors, without exposing the stored sexual-activity answer.
8. Observe staging through at least one 06:00 IST scheduler cycle before enabling production traffic.

## Rollback and operational notes

- Do not automatically downgrade `0010` after hair routines have been created: its downgrade drops the `hair_care_plans` table and therefore its data. If code rollback is needed, redeploy the previous application version while leaving the additive migrations in place; the old code ignores the extra nullable columns/table. Restore a database backup only according to the team's incident procedure.
- `unknown` delivery states mean Meta may or may not have accepted the message. Do not reset them to `pending` casually; reconcile provider status first to avoid duplicates.
- The full test suite could not be collected in this editing environment because `structlog` and `chromadb` are missing here. Focused onboarding/routing/release tests, Python compilation, static release checks and Alembic head discovery were run. Do not treat the missing-dependency full-suite result as a pass; run the full suite in the project's Docker/runtime environment before production rollout.
