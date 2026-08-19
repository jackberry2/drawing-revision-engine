-- Superseded by the pre-existing get_credit_balance(_user_id) RPC,
-- discovered after this view was built (see the introspection done via
-- migration 0008's diagnostic functions). That RPC is the real single
-- source of truth: SECURITY INVOKER, so it's safe both for this service's
-- service-role reads (which deliberately bypass RLS to read an arbitrary
-- user's balance) and for Lovable's frontend to call directly as the
-- authenticated user (RLS on subscriptions/credit_usage still applies to
-- whoever actually calls it). It also encodes real business rules this
-- view's status='active'-only filter got wrong (trialing/past_due/
-- canceled-within-period subscriptions are all real, current subscribers).
-- dre.credits.remaining_credits now calls that RPC directly.

drop view if exists public.user_credit_balance;
