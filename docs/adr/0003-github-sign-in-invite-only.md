# 0003. Sign in with GitHub, invite-only

Date: 2026-11-05

## Status

Accepted

## Context

Members need accounts to edit their profiles and write posts. Every member already has a GitHub account.
Storing passwords would need hashing, a reset flow, and outgoing email.

## Decision

- **GitHub OAuth is the only sign-in method.** The app asks for no scopes. It uses the access token once to
  read the public profile and does not store it.
- **Invite-only.** An admin adds a GitHub login to the `members` table. That row is the invite. The first
  sign-in with that login claims it and records the numeric GitHub ID.
- **Match on the numeric ID after the first sign-in.** A GitHub login can be renamed and then reused by
  another account. The numeric ID never changes. A claimed invite cannot be claimed again by a different
  account with the same login.
- **Bootstrap admins come from `ADMIN_GITHUB_LOGINS`.** This lets the first admin in without a database
  edit.
- **Sessions are signed cookies** (Starlette `SessionMiddleware`) that hold only the member ID. The member
  is loaded from the database on every request, so removing a member ends their access at once.
- **Cross-site request protection comes from `SameSite=Lax`.** Browsers do not send the cookie on
  cross-site POST requests, so another site cannot submit a form as a signed-in member. Every
  state-changing route uses POST.
- **The OAuth `state` parameter** is a random value stored in the session and compared in constant time on
  the callback.

## Consequences

- A member without a GitHub account cannot join.
- A GitHub OAuth app has one callback URL, so local development and production use two OAuth apps.
- A signed cookie cannot be revoked by itself. Removing the member row is the revocation.
- Rotating `SESSION_SECRET` signs everyone out.
