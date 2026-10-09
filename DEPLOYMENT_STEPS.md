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

After both onboarding and successful payment, eligible users receive the temporary confirmation message saying the daily hair-care plan is expected to begin within 2–3 business days. General hair/scalp Q&A is blocked during the hold. Only onboarding, payment handling, a launch-status reply, and emergency triage remain available. No daily plan is generated or sent while the hold is enabled, including stale/manual ARQ jobs, plan revisions, and check-in retries.

The hold does **not** automatically turn off after 2–3 days or after a certain number of users. That avoids silently starting an unreviewed production workflow. Once you are ready, change `HAIR_CARE_LAUNCH_HOLD=false` and restart/redeploy both API and worker. The daily scheduler runs at 06:00 Asia/Kolkata. Every newly purchased paid period remains pending until the first routine under that period is successfully sent; if the WhatsApp 24-hour customer-service window is closed, the saved routine waits for a new inbound message and the paid term does not start yet. Additional paid terms remain pending in payment order while another term is active, then start on the first routine successfully sent under that queued term.

## Smoke test before real users

- Complete the exact manager-approved onboarding wording/options.
- Confirm payment creates one paid-pending subscription (period dates are NULL) and does not start the paid term.
- Confirm the first successfully sent hair-care routine activates the term, setting start/end from the provider-confirmed send time.
- Confirm failed, awaiting-window, and ambiguous sends do not start the paid term.
- Confirm an eligible paid user receives the launch-hold message.
- Confirm an ineligible/safety-gated profile does not receive a promise of an automated plan.
- Confirm Redis has no new `generate_hair_care_plan_for_user` jobs while the hold is enabled.
- Ask a normal hair/scalp question from a paid, onboarded user and verify it receives only the launch-status reply (no Gemini/RAG answer).
- Confirm no daily routine/check-in/revision message is generated or sent during the hold.
- Confirm the hair-only knowledge base rebuild completes against the actual runtime Chroma volume.

## Rollback

Set `HAIR_CARE_LAUNCH_HOLD=true` again and restart/redeploy both API and worker. The app stays available for onboarding and payment handling. General hair/scalp Q&A remains blocked while the hold is enabled. Do not roll back or delete production migrations as a routine rollback method.
