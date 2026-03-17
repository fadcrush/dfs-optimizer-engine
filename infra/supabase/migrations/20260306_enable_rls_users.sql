-- ============================================================
-- Migration: Enable Row Level Security on public.users
-- Resolves: "Table public.users is public, but RLS has not been enabled"
-- Date: 2026-03-06
-- ============================================================

-- 1. Enable RLS
ALTER TABLE public.users ENABLE ROW LEVEL SECURITY;

-- ============================================================
-- 2. Policies
-- auth.uid() returns the UUID of the authenticated Supabase user.
-- The users.id column stores that same UUID, so we match on it.
-- ============================================================

-- 2a. Users can read their own profile only
CREATE POLICY "users_select_own"
  ON public.users
  FOR SELECT
  USING (auth.uid()::text = id);

-- 2b. Users can update their own non-privileged fields
--     (tier/subscription_status intentionally excluded — managed server-side)
CREATE POLICY "users_update_own"
  ON public.users
  FOR UPDATE
  USING (auth.uid()::text = id)
  WITH CHECK (auth.uid()::text = id);

-- 2c. INSERT is performed by the backend service role (registration endpoint).
--     Regular authenticated users cannot insert directly.
CREATE POLICY "users_insert_service_role"
  ON public.users
  FOR INSERT
  WITH CHECK (auth.role() = 'service_role');

-- 2d. DELETE is blocked for all non-service-role callers.
--     Accounts are deactivated via subscription_status, not deleted.
CREATE POLICY "users_delete_service_role"
  ON public.users
  FOR DELETE
  USING (auth.role() = 'service_role');

-- ============================================================
-- 3. Grant the authenticated role access to rows it owns
--    (SELECT / UPDATE only — INSERT/DELETE go through service role)
-- ============================================================
GRANT SELECT, UPDATE ON public.users TO authenticated;
