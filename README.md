# Hair & Scalp Assistant

FastAPI + WhatsApp Cloud API + PostgreSQL + Redis/ARQ + Gemini + ChromaDB + Razorpay. The assistant provides general hair/scalp information and non-medical self-care guidance; it does not diagnose or prescribe.

## Temporary pilot launch hold

`HAIR_CARE_LAUNCH_HOLD=true` is the safe default. Users can complete onboarding and pay, but the application does not generate or send daily routines. Eligible users receive a confirmation that daily hair-care plans are expected to start within 2–3 business days. Every newly purchased paid period stays pending until the first routine under that period is successfully sent; payment date and launch-preparation days do not consume the plan term. Additional paid periods stay queued while another period is active. General hair/scalp Q&A is blocked while the hold is enabled; only onboarding, payment handling, a launch-status reply, and emergency triage remain available. Eligibility/safety exclusions receive a different, non-promissory message.

After the pilot is ready, set `HAIR_CARE_LAUNCH_HOLD=false` for **both the API and ARQ worker**, or deploy the updated `render.yaml`. The normal 06:00 Asia/Kolkata daily routine scheduler then starts. Do not manually enqueue a plan while the hold is enabled.

## Setup / deployment

1. Configure the environment using `.env.example`; secrets must be set in the hosting provider, never committed.
2. Back up PostgreSQL and the configured Chroma directory before rollout.
3. Apply the additive migrations with `alembic upgrade head` (current head: `0011`).
4. Build/rebuild the hair-only knowledge base with `python scripts/build_knowledge_base.py --replace` against the actual runtime Chroma directory.
5. Deploy the API and ARQ worker together. Preserve old database migrations and the `diet_plans` table for historical records/admin audit; daily diet-plan generation code and diet/exercise source data are intentionally removed.
6. Smoke-test consent, exact onboarding wording, payment success, launch-hold message, blocked paid general Q&A (launch-status reply only), emergency triage, no plan generation while held, and scheduler activation after the flag is disabled in staging.

## Knowledge base

Only `data/sources/external/hair_loss_guidelines.json` is currently indexed. It contains paraphrased summaries linked to AAD/NHS guidance. Review medical content and external source terms before public/commercial release. Rebuild the collection after source changes.

## Safety / privacy

- Keep sexual-activity answers out of normal model context and summaries.
- Do not infer diagnosis from onboarding answers.
- Do not recommend prescription medicines, supplement doses, diagnostic tests, or guaranteed regrowth.
- The launch hold is a temporary feature flag; it allows onboarding, consent, and payment, but blocks general Q&A and automated routines until disabled.
