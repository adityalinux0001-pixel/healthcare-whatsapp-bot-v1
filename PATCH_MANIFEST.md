# Hair Assistant launch hold + subscription-start patch

This overlay includes the cleaned Hair & Scalp Assistant setup, temporary launch hold, and subscription-term timing fix. `DELETE_FILES.txt` lists obsolete weight-loss source/code paths to remove in the same Git commit. The full clean project archive already excludes those paths.

**Launch hold:** with `HAIR_CARE_LAUNCH_HOLD=true`, users can complete consent/onboarding and pay, but normal hair/scalp Q&A and all routine generation/delivery are blocked. The bot sends the launch-status reply (or a safety/eligibility-specific notice); inbound messages during hold are stored as redacted placeholders and are not passed to Gemini/RAG. Emergency triage stays available.

**Subscription start:** each newly purchased paid term is saved as `pending` with NULL `start_date`/`end_date`. Its configured `term_days` is snapshotted at purchase. The clock starts only when a routine linked to that paid term is successfully sent through WhatsApp; the send timestamp becomes `start_date` and `end_date` is calculated from that timestamp. Failed, closed-window, or ambiguous sends do not start the term. Additional paid terms remain queued while a prior paid term is active.

Migration `0011` is additive to the existing schema. Its downgrade intentionally refuses to proceed while paid subscriptions are still awaiting first routine delivery, so a rollback cannot silently invent start/end dates for customers who have not received service.

Intentional compatibility retention: Alembic migration history, legacy user profile columns, historical `diet_plans` records/table/model, and no-op ARQ handlers for old queued diet-plan jobs. These avoid breaking production history and rolling deployments.
