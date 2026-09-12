# Standalone DietBot Admin

This is an isolated read-only admin application. It does not import or start the main chatbot application.

## Local setup

1. Create a virtual environment.
2. Install `requirements.txt`.
3. Copy `.env.example` to `.env`.
4. Generate the admin password hash:

   `python generate_password_hash.py 'YOUR_ADMIN_PASSWORD'`

5. Put the generated value in `ADMIN_PASSWORD_HASH` and set a random `ADMIN_SESSION_SECRET`.
6. Set `DATABASE_URL` to the same PostgreSQL database used by the chatbot.
7. Set `REDIS_URL` to the same Redis instance if you want shared login rate limiting.
8. Run `python run_admin.py`.
9. Open `http://127.0.0.1:8001/admin/login`.

## Important

- This dashboard is read-only: it does not update users, subscriptions, or diet plans.
- It requires existing `users`, `subscriptions`, and `diet_plans` tables.
- It does not run migrations.
- In production, put it behind HTTPS and set `ADMIN_COOKIE_SECURE=true`.
- Keep `.env` out of source control.
