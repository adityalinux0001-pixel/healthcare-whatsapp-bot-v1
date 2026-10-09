# Hair assistant launch-hold cleanup manifest

Compared with the uploaded original project: **37 modified files, 14 added files, 16 obsolete files removed**.

The overlay archive includes the modified/new files and `DELETE_FILES.txt`. Remove those obsolete paths from the repository as part of the same commit. The full clean project archive already excludes them.

Intentional compatibility retention: Alembic migration history, legacy user profile columns, the historical `diet_plans` ORM model/table, and no-op ARQ handlers for old queued diet-plan jobs. These are preserved to avoid breaking production records or rolling deployments.
