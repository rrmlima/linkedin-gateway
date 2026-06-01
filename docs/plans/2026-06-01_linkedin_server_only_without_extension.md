# LinkedIn Gateway server-only migration plan

> **For Hermes:** use `subagent-driven-development` or a single GPT-5.4-mini executor task-by-task. Do not jump ahead. Keep the stop condition explicit.

**Goal:** make the LinkedIn Gateway run *entirely server-side* with the Chrome extension closed, while preserving existing behavior as much as possible and failing loudly on any path that still depends on browser/proxy execution.

**Architecture:** keep the current backend as the source of truth, but remove browser-proxy execution from normal request flow. The migration is split into: (1) inventory and classification of remaining proxy/browser dependencies, (2) server-only contract hardening in API schemas and service helpers, (3) endpoint-by-endpoint refactor to eliminate `ws_handler`/`proxy_http_request` from normal operation, and (4) smoke verification with the extension disabled. Any residual proxy path must be treated as diagnostic-only and explicitly opt-in, not a silent fallback.

**Tech Stack:** FastAPI, httpx, SQLAlchemy async, pytest, existing LinkedIn service layer under `backend/app/linkedin/`, existing API routes under `backend/app/api/v1/`, and repo-local shell/Python test commands.

**Achômetro:** 1–3 hours for a strong GPT-5.4-mini executor if the remaining issues are header/session related; risk is medium because the codebase currently mixes server-side calls with proxy fallbacks in several endpoints.

**Stop condition:** stop after one clean pass if the extension is still required anywhere in the normal path. If a test fails, classify the failure first: auth/session, header contract, HTML parsing, websocket/proxy dependency, or endpoint logic. Fix the smallest layer that explains the failure before broadening scope.

---

## Operating rule for this migration

Target operating mode: **server-only**.

That means:
- normal API requests should not require Chrome, the extension, or `ws_handler`
- proxy paths should not be used as silent fallback
- any remaining browser-dependent branch must be explicit, rare, and clearly marked as diagnostic-only
- if a route still cannot run server-side, the code should fail with a precise error rather than quietly routing through the extension

---

## Task 1: Build the dependency map and classify every remaining browser/proxy path

**Objective:** enumerate exactly where the backend still depends on proxy/extension behavior and separate those paths from true server-side paths.

**Files to inspect:**
- `backend/app/api/v1/messages.py`
- `backend/app/api/v1/reactions.py`
- `backend/app/api/v1/comments.py`
- `backend/app/api/v1/posts.py`
- `backend/app/api/v1/profiles.py`
- `backend/app/api/v1/profile_identity.py`
- `backend/app/api/v1/profile_contact.py`
- `backend/app/api/v1/profile_about_skills.py`
- `backend/app/api/v1/user_comments.py`
- `backend/app/api/v1/connections.py`
- `backend/app/api/v1/utils.py`
- `backend/app/api/v1/server_validation.py`
- `backend/app/linkedin/utils/my_profile_id.py`
- `backend/app/linkedin/helpers/proxy_http.py`
- `backend/app/linkedin/helpers/refresh_session.py`
- `backend/app/linkedin/utils/profile_id_extractor.py`
- `backend/app/schemas/profile.py`
- `backend/app/schemas/post.py`
- `backend/app/schemas/connection.py`

**What to look for:**
- `server_call` defaults and branch logic
- `use_proxy` branches
- `ws_handler` requirements
- `proxy_http_request(...)`
- any path that still assumes the extension is present
- any HTML fetch using the wrong header contract for server-side navigation

**Verification command:**
```bash
cd /root/linkedin-gateway
python - <<'PY'
from pathlib import Path
import re
root = Path('backend/app')
patterns = [r'server_call', r'use_proxy', r'ws_handler', r'proxy_http_request', r'headers=self\.headers', r'headers=.*service\.headers']
for p in root.rglob('*.py'):
    text = p.read_text()
    if any(re.search(pat, text) for pat in patterns):
        print(p)
PY
```

**Expected result:** a short list of true remaining proxy dependencies, not just the already-reviewed server-side HTML/header routes.

---

## Task 2: Make server-only the default contract at the API boundary

**Objective:** make every public request schema and route express that server-side execution is the normal path.

**Files to modify:**
- `backend/app/schemas/profile.py`
- `backend/app/schemas/post.py`
- `backend/app/schemas/connection.py`
- `backend/app/api/v1/messages.py`
- `backend/app/api/v1/reactions.py`
- `backend/app/api/v1/comments.py`
- `backend/app/api/v1/posts.py`
- `backend/app/api/v1/profiles.py`
- `backend/app/api/v1/profile_identity.py`
- `backend/app/api/v1/profile_contact.py`
- `backend/app/api/v1/profile_about_skills.py`
- `backend/app/api/v1/user_comments.py`
- `backend/app/api/v1/connections.py`
- `backend/app/api/v1/utils.py`

**Implementation rules:**
- default request models to server-side operation
- do not let the route silently switch to proxy if the extension is missing
- if the executor keeps the `server_call` field for compatibility, the route must either:
  - treat `False` as deprecated and fail with a precise error, or
  - map it to the same server-side execution path only if that is clearly safe for the endpoint
- keep the error message explicit: “browser-proxy mode is disabled in server-only deployment” or equivalent

**Verification command:**
```bash
cd /root/linkedin-gateway
PYTHONPATH=. pytest -q backend/tests/test_messages_headers.py backend/tests/test_comments_headers.py backend/tests/test_comments_write_headers.py backend/tests/test_reactions_like_endpoints.py
```

**Expected result:** existing server-side tests still pass, and any proxy-only assumptions are surfaced clearly rather than hidden.

---

## Task 3: Remove `ws_handler`/proxy dependency from the authenticated profile-ID path

**Objective:** make `my_profile_id` work without the extension for the normal path.

**Files to modify:**
- `backend/app/linkedin/utils/my_profile_id.py`
- `backend/app/api/v1/messages.py`
- `backend/app/api/v1/reactions.py`
- any other route that calls `get_my_profile_id_with_fallbacks(...)`

**What to change:**
- make the `SERVER_CALL` path the canonical one
- keep the profile-ID retrieval on the backend using GraphQL/API-only calls
- remove the normal dependency on `ws_handler` and `proxy_http_request`
- if a fallback remains, place it behind an explicit diagnostic flag or clearly marked internal-only branch
- ensure session refresh logic does not require the browser path during normal operation

**Important edge cases to verify:**
- 401/403 handling when the LinkedIn session is stale
- retry behavior when the first GraphQL query fails
- cache read/write behavior still works
- proxy-specific code is not hit during the normal server-only path

**Focused tests to add or update:**
- new unit test for server-only `get_my_profile_id_with_fallbacks(..., use_proxy=False)` with no `ws_handler`
- test that a stale session yields a clear error or refresh attempt without requiring the extension
- test that the proxy path is not required for the successful case

**Verification command:**
```bash
cd /root/linkedin-gateway
PYTHONPATH=. pytest -q backend/tests/test_messages_headers.py backend/tests/test_reactions_like_endpoints.py
```

Then run any new test file you add for `my_profile_id`.

---

## Task 4: Make the remaining HTML/profile extraction paths fully server-side and browser-like only by headers

**Objective:** keep the HTML/profile extraction flows, but ensure they use the correct browser-like header contract and never route through the extension.

**Files to review/adjust:**
- `backend/app/api/v1/utils.py`
- `backend/app/api/v1/messages.py`
- `backend/app/linkedin/services/base.py`
- `backend/app/linkedin/services/posts.py`
- `backend/app/linkedin/services/profile.py`
- `backend/app/linkedin/services/profile_identity.py`
- `backend/app/linkedin/services/profile_contact.py`
- `backend/app/linkedin/services/profile_about_skills.py`
- `backend/app/linkedin/services/user_comments.py`
- `backend/app/linkedin/services/connections.py`
- `backend/app/linkedin/utils/profile_id_extractor.py`

**What to check:**
- all HTML fetches use browser-like headers, not API-only headers
- no HTML fetch relies on `proxy_http_request`
- `profile_id_extractor` remains purely server-side and is fed the right headers
- the profile/contact/about/comments surfaces still resolve IDs without extension access

**Focused verification commands:**
```bash
cd /root/linkedin-gateway
PYTHONPATH=. pytest -q backend/tests/test_posts_page_headers.py backend/tests/test_profile_posts_service.py backend/tests/test_linkedin_profile_page_header_routing.py
```

If a failure appears, classify it first:
- wrong headers
- wrong URL
- HTML parser regression
- stale auth/session
- hidden proxy dependency

---

## Task 5: Eliminate proxy as a silent fallback in write endpoints

**Objective:** ensure comments, reactions, messages, connections, and similar write operations do not silently depend on the extension.

**Files to review/adjust:**
- `backend/app/api/v1/comments.py`
- `backend/app/api/v1/reactions.py`
- `backend/app/api/v1/messages.py`
- `backend/app/api/v1/connections.py`
- `backend/app/api/v1/posts.py`
- `backend/app/api/v1/server_validation.py`
- `backend/app/linkedin/helpers/proxy_http.py`

**What to change:**
- server-side execution should not require the websocket service
- proxy fallback should not be chosen automatically
- if `server_call=False` is still accepted for compatibility, make the behavior explicit and predictable
- validate any remaining headers/body formats against the real server-side requests already covered by tests

**Focused tests to run after each change:**
```bash
cd /root/linkedin-gateway
PYTHONPATH=. pytest -q backend/tests/test_comments_get_commenters_retry.py backend/tests/test_comments_headers.py backend/tests/test_comments_write_headers.py backend/tests/test_reactions_like_endpoints.py backend/tests/test_messages_headers.py
```

**Expected result:** all normal server-side tests pass with the extension stopped.

---

## Task 6: Add a real no-extension smoke suite

**Objective:** prove the backend can operate with Chrome closed.

**Files to add or extend:**
- `backend/tests/test_server_only_smoke.py` or a similarly named focused test file
- any small helper fixtures needed for server-only runs

**Smoke cases to cover:**
1. authenticated profile ID lookup on server only
2. one HTML profile extraction path
3. one read path from posts/profile surface
4. one write path from comments or reactions
5. explicit failure when a proxy-only path is invoked without the extension, if that path remains

**Required verification:**
- run the smoke suite with the extension stopped
- record exact failures, if any
- fix only the smallest layer needed to make the server-only path work

**Suggested command:**
```bash
cd /root/linkedin-gateway
PYTHONPATH=. pytest -q backend/tests/test_server_only_smoke.py
```

If no smoke file exists yet, create it before running this task.

---

## Task 7: Final regression pass, diff hygiene, and commit

**Objective:** make sure the migration is coherent, reproducible, and safe to ship.

**Checks to run:**
```bash
cd /root/linkedin-gateway
git diff --check
git status --short --branch
PYTHONPATH=. pytest -q \
  backend/tests/test_comments_get_commenters_retry.py \
  backend/tests/test_comments_headers.py \
  backend/tests/test_comments_write_headers.py \
  backend/tests/test_reactions_like_endpoints.py \
  backend/tests/test_messages_headers.py \
  backend/tests/test_posts_page_headers.py \
  backend/tests/test_profile_posts_service.py \
  backend/tests/test_linkedin_profile_page_header_routing.py
```

**Expected result:**
- clean diff check
- working tree clean or only intentional changes remaining
- focused test set passes
- commit message reflects server-only migration, not a generic header tweak

**Commit guidance:**
- one coherent commit if the scope stays contained
- if the migration needs to be split, commit after the first stable server-only boundary and again after the smoke suite passes

---

## Rollback path

If a change breaks a flow:
1. identify the exact endpoint and execution mode that failed
2. classify whether the failure is auth/session, header contract, HTML parsing, or proxy/websocket dependency
3. revert only the last minimal change that introduced the regression
4. rerun the focused test file before touching the next layer

Do **not** broaden the rollback to unrelated files unless the failure proves the shared contract is wrong.

---

## Definition of done

This migration is done only when:
- the extension can stay closed for normal use
- server-side paths succeed without `ws_handler`
- no normal route silently falls back to proxy/browser execution
- the focused tests pass
- the final smoke run proves the backend works in server-only mode
- any remaining proxy path is explicitly marked diagnostic-only or removed
