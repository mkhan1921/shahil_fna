-- Notifications are read per run (the API's run view, the activity's duplicate check); the primary key
-- is the idempotency key, so the per-run lookup needs its own index.
CREATE INDEX IF NOT EXISTS notifications_run_idx ON notifications (workflow_id, created_at);
