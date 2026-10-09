# Launch-hold deployment steps

This release intentionally keeps routine generation disabled until the pilot is ready.

## Before deploying

1. Back up PostgreSQL and your live Chroma directory/volume.
2. Review the diff and apply the cleaned source files to the existing repository. Use `DELETE_FILES.txt` to remove the obsolete files listed there; do not delete migration history or database tables.
3. Keep `HAIR_CARE_LAUNCH_HOLD=true` in the API environment. The Render worker inherits this value from the API service; for Docker Compose, keep it `true` in `.env` used by both `app` and `worker`.
4. Deploy the API and worker together. In Render, the existing `preDeployCommand: alembic upgrade head` remains responsible for applying additive migrations.
5. Rebuild the hair-only knowledge base from `data/sources/external/hair_loss_guidelines.json`.

### Docker Compose with an existing persistent Chroma volume

The named `knowledge_data` volume may contain the old diet/exercise vectors. Stop app/worker before replacing that collection, leaving PostgreSQL and Redis running:

```bash
docker compose stop app worker
docker compose run --rm app python scripts/build_knowledge_base.py --replace
docker compose up -d --build app worker
```

Back up the Chroma volume first. `--replace` clears the current Chroma collection and replaces it with the included hair/scalp dataset. Do not run this against a shared environment unless you intend to replace its collection.

## Expected pilot behavior

After both onboarding and successful payment, eligible users receive the temporary confirmation message saying the daily hair-care plan is expected to begin within 2–3 business days. The chatbot can still answer general hair/scalp questions. No daily plan is generated or sent while the hold is enabled, including stale/manual ARQ jobs, plan revisions, and check-in retries.

The hold does **not** automatically turn off after 2–3 days or after a certain number of users. That avoids silently starting an unreviewed production workflow. Once you are ready, change `HAIR_CARE_LAUNCH_HOLD=false` and restart/redeploy both API and worker. The daily scheduler runs at 06:00 Asia/Kolkata; an individual user may receive a saved routine later if the WhatsApp 24-hour customer-service window is closed.

## Smoke test before real users

- Complete the exact manager-approved onboarding wording/options.
- Confirm the payment webhook activates the subscription once.
- Confirm an eligible paid user receives the launch-hold message.
- Confirm an ineligible/safety-gated profile does not receive a promise of an automated plan.
- Confirm Redis has no new `generate_hair_care_plan_for_user` jobs while the hold is enabled.
- Ask a normal hair/scalp question and verify it still receives a response.
- Confirm no daily routine/check-in/revision message is generated or sent during the hold.
- Confirm the hair-only knowledge base rebuild completes against the actual runtime Chroma volume.

## Rollback

Set `HAIR_CARE_LAUNCH_HOLD=true` again and restart/redeploy both API and worker. The app stays available for onboarding, payment, and general Q&A. Do not roll back or delete production migrations as a routine rollback method.
