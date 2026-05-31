"""
LinkedIn Posts API service.

Provides server-side implementation of post operations including text extraction from HTML.
"""
from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import math
import random
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from typing import Any, Dict, List, Optional

import httpx

from ..utils.profile_id_extractor import extract_profile_id
from .base import LinkedInServiceBase
from .feed import LinkedInFeedService

logger = logging.getLogger(__name__)


class LinkedInPostsService(LinkedInServiceBase):
    """Service for LinkedIn post operations."""

    def _format_post_age(self, timestamp_iso: Optional[str]) -> Optional[str]:
        """Format an ISO timestamp into a rough LinkedIn-style age string."""
        if not timestamp_iso:
            return None

        try:
            dt = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = now - dt.astimezone(timezone.utc)
            days = max(delta.days, 0)

            if days < 1:
                hours = max(delta.seconds // 3600, 0)
                return f"{hours}h" if hours > 0 else "0h"
            if days < 7:
                return f"{days}d"
            if days < 30:
                return f"{max(days // 7, 1)}w"
            if days < 365:
                return f"{max(days // 30, 1)}mo"
            return f"{max(days // 365, 1)}y"
        except Exception:
            logger.debug("[PROFILE_POSTS] Failed to format post age", exc_info=True)
            return None

    def extract_post_text_from_html(self, html_content: str) -> Optional[str]:
        """
        Extract post text from LinkedIn HTML response.

        LinkedIn embeds JSON data in <code> tags with IDs like 'bpr-guid-XXXXX'.
        The post text is typically in objects with types:
        - com.linkedin.voyager.dash.feed.Update
        - com.linkedin.voyager.dash.feed.ShareUpdate

        The text is located at:
        - commentary.text.text (nested format)
        - commentary.text (direct string format)

        Args:
            html_content: Raw HTML content from LinkedIn post page

        Returns:
            Post text string if found, None otherwise
        """
        if not html_content:
            logger.warning("[EXTRACT_POST_TEXT] Empty HTML content provided")
            return None

        logger.info(f"[EXTRACT_POST_TEXT] Processing HTML content ({len(html_content)} bytes)")

        code_pattern = r'<code[^>]*id="bpr-guid-\d+"[^>]*>(.*?)</code>'
        code_blocks = re.findall(code_pattern, html_content, re.DOTALL)

        logger.info(f"[EXTRACT_POST_TEXT] Found {len(code_blocks)} code blocks to analyze")

        for idx, code_block in enumerate(code_blocks):
            try:
                decoded_content = html_module.unescape(code_block)

                try:
                    data = json.loads(decoded_content)
                except json.JSONDecodeError:
                    continue

                included = data.get('included', [])
                if not isinstance(included, list):
                    continue

                for item in included:
                    if not isinstance(item, dict):
                        continue

                    item_type = item.get('$type', '')
                    if item_type not in [
                        'com.linkedin.voyager.dash.feed.Update',
                        'com.linkedin.voyager.dash.feed.ShareUpdate'
                    ]:
                        continue

                    commentary = item.get('commentary', {})
                    if not isinstance(commentary, dict):
                        continue

                    text_field = commentary.get('text')
                    if isinstance(text_field, dict):
                        post_text = text_field.get('text')
                        if post_text and isinstance(post_text, str):
                            logger.info(
                                f"[EXTRACT_POST_TEXT] ✓ Found post text (nested format) in block {idx}: {post_text[:100]}..."
                            )
                            return post_text
                    elif isinstance(text_field, str):
                        logger.info(
                            f"[EXTRACT_POST_TEXT] ✓ Found post text (direct format) in block {idx}: {text_field[:100]}..."
                        )
                        return text_field

            except Exception as e:
                logger.debug(f"[EXTRACT_POST_TEXT] Error processing code block {idx}: {e}")
                continue

        logger.warning("[EXTRACT_POST_TEXT] Post text not found in any code block")
        return None

    async def fetch_post_html(self, post_url: str) -> str:
        """
        Fetch the HTML content of a LinkedIn post page.

        Args:
            post_url: Full URL of the LinkedIn post

        Returns:
            HTML content as string
        """
        import httpx

        logger.info(f"[FETCH_POST_HTML] Fetching HTML for URL: {post_url}")

        async with httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True
        ) as client:
            try:
                response = await client.request(
                    method='GET',
                    url=post_url,
                    headers=self.headers
                )

                logger.info(f"[FETCH_POST_HTML] Response status: {response.status_code}")
                response.raise_for_status()

                html_content = response.text
                logger.info(f"[FETCH_POST_HTML] Received HTML response ({len(html_content)} bytes)")

                return html_content

            except httpx.HTTPStatusError as e:
                logger.error(f"[FETCH_POST_HTML] HTTP error: {e.response.status_code}")
                raise
            except httpx.TimeoutException:
                logger.error(f"[FETCH_POST_HTML] Request timed out after {self.TIMEOUT}s")
                raise
            except Exception as e:
                logger.error(f"[FETCH_POST_HTML] Request failed: {str(e)}")
                raise

    def _extract_vanity_from_input(self, profile_id_or_url: str) -> Optional[str]:
        text = (profile_id_or_url or '').strip().rstrip('/')
        if not text:
            return None
        match = re.search(r'linkedin\.com/in/([^/?#]+)', text)
        if match:
            return match.group(1)
        if '/' not in text and not text.startswith('ACo') and not text.startswith('urn:li:'):
            return text
        return None

    def _extract_tracking_id(self, value: Optional[str]) -> Optional[str]:
        text = str(value or '')
        patterns = [
            r'urn:li:activity:(\d+)',
            r'urn:li:ugcPost:(\d+)',
            r'urn:li:article:(\d+)',
            r'activity:(\d+)',
            r'activity-(\d+)',
            r'ugcPost:(\d+)',
            r'ugcPost-(\d+)',
            r'article:(\d+)',
            r'article-(\d+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1)
        if text.isdigit():
            return text
        return None

    def _deep_text(self, value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            for key in ('text', 'value', 'name', 'title', 'subtitle'):
                text = self._deep_text(value.get(key))
                if text:
                    return text
        return ''

    def _pick_first_url(self, item: Dict[str, Any]) -> Optional[str]:
        for key in ('postUrl', 'url', 'shareUrl', 'navigationUrl'):
            value = item.get(key)
            if isinstance(value, str) and 'linkedin.com' in value:
                return value
        return None

    def _coerce_datetime(self, value: Any) -> Optional[datetime]:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc)
        if isinstance(value, (int, float)):
            try:
                return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
            except Exception:
                return None
        text = str(value).strip()
        if not text:
            return None
        if text.isdigit():
            try:
                return datetime.fromtimestamp(int(text) / 1000.0, tz=timezone.utc)
            except Exception:
                return None
        try:
            parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    def _activity_is_recent(self, timestamp_value: Any, window_days: int = 30) -> bool:
        dt = self._coerce_datetime(timestamp_value)
        if not dt:
            return True
        now = datetime.now(timezone.utc)
        return (now - dt) <= timedelta(days=window_days)

    def _normalize_activity_item(self, item: Dict[str, Any], identity: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        raw_id = item.get('entityUrn') or item.get('urn') or item.get('postId') or item.get('backendUrn')
        tracking_id = self._extract_tracking_id(raw_id)
        url = self._pick_first_url(item)
        if not tracking_id and url:
            tracking_id = self._extract_tracking_id(url)
        if not tracking_id:
            return None

        post_id = str(raw_id) if raw_id and str(raw_id).startswith('urn:li:') else f'urn:li:activity:{tracking_id}'
        post_url = url or f'https://www.linkedin.com/feed/update/urn:li:activity:{tracking_id}/'

        commentary = item.get('commentary') if isinstance(item.get('commentary'), dict) else {}
        text = self._deep_text(commentary.get('text')) or self._deep_text(item.get('commentary')) or self._deep_text(item.get('text'))
        actor = item.get('actor') if isinstance(item.get('actor'), dict) else {}
        author_name = self._deep_text(actor.get('name')) or self._deep_text(item.get('authorName')) or identity.get('name') or ''

        social_detail = item.get('socialDetail') if isinstance(item.get('socialDetail'), dict) else {}
        counts_data = social_detail.get('totalSocialActivityCounts') if isinstance(social_detail, dict) else None
        counts = counts_data if isinstance(counts_data, dict) else {}

        update_metadata: Any = item.get('updateMetadata') if isinstance(item.get('updateMetadata'), dict) else {}
        timestamp_value = (
            item.get('createdAt')
            or item.get('publishedAt')
            or item.get('timestamp')
            or update_metadata.get('timestamp')
        )
        dt = self._coerce_datetime(timestamp_value)
        post_date = dt.isoformat().replace('+00:00', 'Z') if dt else (str(timestamp_value) if timestamp_value else None)

        return {
            'postId': post_id,
            'postUrl': post_url,
            'ugcPostId': f'urn:li:ugcPost:{tracking_id}' if 'ugcPost' in post_id else None,
            'articleId': None,
            'videoAssetId': None,
            'authorName': author_name,
            'authorProfileId': identity.get('profile_id'),
            'postContent': text or '',
            'postDate': post_date,
            'postAge': self._format_post_age(post_date),
            'likes': int(counts.get('numLikes') or counts.get('likes') or item.get('likes') or 0),
            'comments': int(counts.get('numComments') or counts.get('comments') or item.get('comments') or 0),
            'imageUrl': None,
            'isVideo': bool(item.get('isVideo') or item.get('hasVideo')),
            'hasVideo': bool(item.get('hasVideo') or item.get('isVideo')),
            'videoType': item.get('videoType'),
        }

    def _walk_activity_candidates(self, value: Any, found: List[Dict[str, Any]]) -> None:
        if isinstance(value, dict):
            item_type = str(value.get('$type') or '')
            raw = ' '.join(str(value.get(k) or '') for k in ('entityUrn', 'urn', 'postId', 'backendUrn', 'navigationUrl', 'shareUrl'))
            type_is_update = 'update' in item_type.lower() or item_type.endswith('Update')
            if type_is_update or 'urn:li:activity:' in raw or 'urn:li:ugcPost:' in raw:
                found.append(value)
            for nested in value.values():
                self._walk_activity_candidates(nested, found)
        elif isinstance(value, list):
            for nested in value:
                self._walk_activity_candidates(nested, found)

    def _parse_profile_activity_html(self, html_content: str, identity: Dict[str, Any]) -> List[Dict[str, Any]]:
        candidate_blocks: List[str] = []
        for tag in ('code', 'script'):
            pattern = rf'<{tag}[^>]*>(.*?)</{tag}>'
            candidate_blocks.extend(re.findall(pattern, html_content or '', re.DOTALL))

        logger.debug('[PROFILE_POSTS] Activity HTML candidate blocks: %s', len(candidate_blocks))
        logger.debug('[PROFILE_POSTS] Activity HTML raw markers: commentary=%s authorProfileId=%s shareUrl=%s navigationUrl=%s',
                    html_content.count('commentary'),
                    html_content.count('authorProfileId'),
                    html_content.count('shareUrl'),
                    html_content.count('navigationUrl'))
        candidates: List[Dict[str, Any]] = []
        for block in candidate_blocks:
            decoded = html_module.unescape(block).strip()
            payload = None
            for candidate_json in (decoded,):
                try:
                    payload = json.loads(candidate_json)
                    break
                except Exception:
                    continue
            if payload is None:
                starts = [decoded.find('{'), decoded.find('[')]
                starts = [idx for idx in starts if idx >= 0]
                if starts:
                    start = min(starts)
                    end = max(decoded.rfind('}'), decoded.rfind(']'))
                    if end > start:
                        fragment = decoded[start : end + 1]
                        try:
                            payload = json.loads(fragment)
                        except Exception:
                            payload = None
            if payload is None:
                continue
            self._walk_activity_candidates(payload, candidates)

        # Fallback: if LinkedIn wrapped the JSON differently, extract minimal
        # URN-based candidates from the raw page HTML.
        if not candidates:
            for match in re.finditer(r'urn:li:(?:activity|ugcPost|article):(\d+)', html_content or ''):
                tracking_id = match.group(1)
                candidates.append({'entityUrn': f'urn:li:activity:{tracking_id}'})

        logger.debug('[PROFILE_POSTS] Activity HTML candidate items: %s', len(candidates))
        normalized: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            post = self._normalize_activity_item(item, identity)
            if not post:
                continue
            tracking_id = self._extract_tracking_id(post.get('postId') or post.get('postUrl') or '')
            key = tracking_id or post['postUrl']
            if key in seen:
                continue
            seen.add(key)
            normalized.append(post)
        logger.info('[PROFILE_POSTS] Activity HTML normalized posts: %s', len(normalized))
        if not normalized and candidates:
            sample = []
            for item in candidates[:3]:
                if isinstance(item, dict):
                    sample.append({k: item.get(k) for k in ('$type', 'entityUrn', 'urn', 'postId', 'backendUrn', 'navigationUrl', 'shareUrl', 'postUrl', 'commentary', 'text') if k in item})
                else:
                    sample.append(str(type(item)))
            logger.debug('[PROFILE_POSTS] Activity HTML sample candidates: %s', sample)
        return normalized

    async def _resolve_profile_identity(self, profile_id_or_url: str) -> Dict[str, Any]:
        vanity = self._extract_vanity_from_input(profile_id_or_url)
        profile_url = f'https://www.linkedin.com/in/{vanity}/' if vanity else None
        profile_id = None

        try:
            profile_id = await extract_profile_id(
                profile_input=profile_id_or_url,
                headers=self.headers,
                timeout=self.TIMEOUT,
            )
        except Exception as exc:
            logger.info('[PROFILE_POSTS] Could not resolve profile ID directly: %s: %s', type(exc).__name__, exc)

        if not vanity:
            try:
                from .profile import LinkedInProfileService
                profile_service = LinkedInProfileService(self.csrf_token, self.linkedin_cookies)
                profile_data = await profile_service.scrape_profile(profile_id_or_url)
                vanity = profile_data.get('vanity_name') or profile_data.get('profile_url', '').rstrip('/').split('/')[-1]
                profile_url = profile_data.get('profile_url') or profile_url
                profile_id = profile_id or profile_data.get('linkedin_id')
            except Exception as exc:
                logger.info('[PROFILE_POSTS] Could not resolve vanity name from profile scrape: %s: %s', type(exc).__name__, exc)

        if not vanity and profile_url:
            vanity = profile_url.rstrip('/').split('/')[-1]

        return {
            'profile_id': profile_id,
            'vanity': vanity,
            'profile_url': profile_url,
        }

    async def _fetch_profile_activity_html(self, vanity: str) -> str:
        if not vanity:
            raise ValueError('Cannot fetch profile activity without a vanity name')
        url = f'https://www.linkedin.com/in/{vanity}/recent-activity/posts/'
        logger.info('[PROFILE_POSTS] Fetching direct activity page: %s', url)
        async with httpx.AsyncClient(timeout=self.TIMEOUT, follow_redirects=True) as client:
            response = await client.get(url, headers=self.headers)
            response.raise_for_status()
            return response.text

    async def _fetch_profile_activity_posts(self, identity: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
        vanity = identity.get('vanity')
        if not vanity:
            return []
        html_content = await self._fetch_profile_activity_html(vanity)
        posts = self._parse_profile_activity_html(html_content, identity)
        posts = [post for post in posts if self._activity_is_recent(post.get('postDate'), 30)]
        return self._dedupe_and_sort_profile_posts(posts)[:count]

    async def _fetch_profile_share_feed_posts(self, identity: Dict[str, Any], count: int) -> List[Dict[str, Any]]:
        profile_id = (identity.get('profile_id') or '').strip()
        if not profile_id:
            return []

        page_size = 20
        max_pages = max(1, math.ceil(count / page_size))
        encoded_profile_urn = quote(f'urn:li:fsd_profile:{profile_id}', safe='')
        query_id = 'voyagerFeedDashProfileUpdates.418845c51162bcbbda12be537ccc7976'

        all_posts: List[Dict[str, Any]] = []
        start = 0
        pagination_token = ''
        has_more = True
        empty_responses = 0
        session_page_count = 0

        while has_more and session_page_count < max_pages and len(all_posts) < count:
            variables = f'(count:{page_size},start:{start},profileUrn:{encoded_profile_urn}'
            if pagination_token:
                variables += f',paginationToken:{quote(pagination_token, safe="")}'
            variables += ')'
            url = f'{self.GRAPHQL_BASE_URL}?variables={variables}&queryId={query_id}'
            logger.debug('[PROFILE_POSTS] Share-feed request page=%s url=%s', session_page_count + 1, url[:180])

            data = await self._make_request(
                url,
                method='GET',
                timeout=self.TIMEOUT,
                headers=self.headers,
                debug_endpoint_type='profile_posts_share_feed',
            )

            feed_data = {}
            if isinstance(data, dict):
                feed_data = (((data.get('data') or {}).get('data') or {}).get('feedDashProfileUpdatesByMemberShareFeed') or {})
                if not feed_data:
                    feed_data = (((data.get('data') or {}).get('data') or {}).get('feedDashProfileUpdatesByMemberComments') or {})

            if not isinstance(feed_data, dict):
                empty_responses += 1
                if empty_responses >= 3:
                    break
                start += page_size
                session_page_count += 1
                continue

            elements = feed_data.get('*elements') or feed_data.get('elements') or []
            included = data.get('included', []) if isinstance(data, dict) else []
            if not isinstance(included, list):
                included = []

            logger.debug('[PROFILE_POSTS] Share-feed response: elements=%s included=%s', len(elements) if isinstance(elements, list) else 0, len(included))

            candidates: List[Dict[str, Any]] = []
            self._walk_activity_candidates(feed_data, candidates)
            self._walk_activity_candidates(included, candidates)
            self._walk_activity_candidates(data, candidates)

            page_posts: List[Dict[str, Any]] = []
            seen: set[str] = set()
            for item in included:
                if not isinstance(item, dict):
                    continue
                item_type = str(item.get('$type') or '')
                if 'update' not in item_type.lower() and not item.get('updateMetadata'):
                    continue
                post = self._normalize_activity_item(item, identity)
                if not post:
                    continue
                tracking_id = self._extract_tracking_id(post.get('postId') or post.get('postUrl') or '')
                key = tracking_id or post['postUrl']
                if key in seen:
                    continue
                seen.add(key)
                page_posts.append(post)

            page_posts = [post for post in page_posts if self._activity_is_recent(post.get('postDate'), 30)]
            if page_posts:
                all_posts.extend(page_posts)
                empty_responses = 0
            else:
                empty_responses += 1
                if empty_responses >= 3:
                    break

            metadata = feed_data.get('metadata', {}) if isinstance(feed_data, dict) else {}
            paging = feed_data.get('paging', {}) if isinstance(feed_data, dict) else {}
            if isinstance(metadata, dict) and metadata.get('paginationToken'):
                pagination_token = metadata['paginationToken']
            if isinstance(paging, dict) and paging.get('start') is not None:
                new_start = paging.get('start')
                if isinstance(new_start, int):
                    start = new_start if new_start != start else start + page_size
                else:
                    start += page_size
            else:
                start += page_size
                has_more = False

            session_page_count += 1

        return self._dedupe_and_sort_profile_posts(all_posts)[:count]

    async def _scan_feed_for_profile_posts(
        self,
        identity: Dict[str, Any],
        count: int,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
    ) -> List[Dict[str, Any]]:
        return await self._fetch_profile_share_feed_posts(identity, count)

    def _dedupe_and_sort_profile_posts(self, posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        unique: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for post in posts:
            tracking_id = self._extract_tracking_id(post.get('postId') or post.get('postUrl') or '')
            key = tracking_id or post.get('postUrl') or post.get('postId')
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(post)

        def sort_key(post: Dict[str, Any]):
            dt = self._coerce_datetime(post.get('postDate'))
            if dt:
                return (1, dt.timestamp())
            return (0, str(post.get('postDate') or ''))

        return sorted(unique, key=sort_key, reverse=True)

    async def fetch_posts_for_profile(
        self,
        profile_id_or_url: str,
        count: int,
        min_delay: float = 2.0,
        max_delay: float = 5.0,
    ) -> Dict[str, Any]:
        """
        Fetch posts for a LinkedIn profile using a source ladder.

        Primary source: direct profile activity page.
        Secondary source: bounded authenticated feed scan filtered by authorProfileId.
        """
        if not isinstance(count, int) or count <= 0:
            raise ValueError(f"Invalid count: {count}. Must be a positive integer.")

        logger.info('[PROFILE_POSTS] Fetching up to %s posts for profile input: %s', count, profile_id_or_url)
        identity = await self._resolve_profile_identity(profile_id_or_url)
        profile_id = identity.get('profile_id')
        diagnostics: List[str] = []

        collected: List[Dict[str, Any]] = []
        try:
            collected = await self._fetch_profile_activity_posts(identity, count)
            diagnostics.append(f'direct_activity returned {len(collected)} post(s)')
        except Exception as exc:
            logger.warning('[PROFILE_POSTS] Direct activity source failed: %s: %s', type(exc).__name__, exc)
            diagnostics.append(f'direct_activity failed: {type(exc).__name__}')
            collected = []

        if not collected and (profile_id or identity.get('vanity')):
            try:
                collected = await self._scan_feed_for_profile_posts(identity, count, min_delay=min_delay, max_delay=max_delay)
                diagnostics.append(f'share_feed returned {len(collected)} post(s)')
            except Exception as exc:
                logger.warning('[PROFILE_POSTS] Feed scan fallback failed: %s: %s', type(exc).__name__, exc)
                diagnostics.append(f'feed_scan failed: {type(exc).__name__}')
                collected = []

        collected = self._dedupe_and_sort_profile_posts(collected)[:count]
        logger.info('[PROFILE_POSTS] Finished with %s post(s); diagnostics=%s', len(collected), '; '.join(diagnostics))
        return {
            'posts': collected,
            'hasMore': len(collected) >= count,
            'paginationToken': None,
        }
