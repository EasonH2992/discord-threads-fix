import httpx
from bs4 import BeautifulSoup
import re
import asyncio
import io
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# The Threads /embed page prints its timestamp in Meta's own server timezone
# (US Pacific), not the viewer's locale — Accept-Language only changes the
# "上午/下午" wording and date format, not the actual clock time. Use a real
# IANA zone (not a fixed UTC-7/-8 offset) so DST transitions stay correct.
_TZ_EMBED = ZoneInfo("America/Los_Angeles")

_USER_AGENTS = [
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "WhatsApp/2.21.12.21 A",
    "TelegramBot (like TwitterBot)",
    "LinkedInBot/1.0 (compatible; Mozilla/5.0; Apache-HttpClient/4.2.1; +http://www.linkedin.com)",
    "Slackbot-LinkExpanding 1.0 (+https://api.slack.com/robots)",
    "curl/8.7.1",
]

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/137.0.0.0 Safari/537.36"
)

def _load_cookies() -> dict | None:
    path = os.path.join(os.path.dirname(__file__), "cookies.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        cookies = {
            c["name"]: c["value"]
            for c in state.get("cookies", [])
            if "threads.com" in c.get("domain", "") or "instagram.com" in c.get("domain", "")
        }
        if cookies:
            print(f"[scraper] Loaded {len(cookies)} cookies from cookies.json")
        return cookies or None
    except Exception as e:
        print(f"[scraper] Failed to load cookies.json: {e}")
        return None

_COOKIES = _load_cookies()


def _parse_threads_timestamp(text: str):
    """Parse Threads embed page's localized timestamp (e.g. '上午1:17 · 2026年7月27日')
    into an aware UTC datetime. Threads stopped including a machine-readable
    "taken_at" epoch anywhere in the page, so this text is the only source left."""
    if not text:
        return None
    m = re.match(r"(上午|下午)\s*(\d{1,2}):(\d{2})\s*[·・]\s*(\d{4})年(\d{1,2})月(\d{1,2})日", text.strip())
    if not m:
        return None
    ampm, hh, mm, yyyy, mo, dd = m.groups()
    hh = int(hh)
    if ampm == "上午":
        if hh == 12:
            hh = 0
    else:
        if hh != 12:
            hh += 12
    try:
        dt = datetime(int(yyyy), int(mo), int(dd), hh, int(mm), tzinfo=_TZ_EMBED)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc)


async def _fetch_threads_embed_extra(embed_url: str):
    """Threads no longer ships carousel/media JSON in the regular post page's SSR
    HTML, so full-resolution images, video, caption and timestamp are instead
    scraped from the official /embed widget page, which is still server-rendered."""
    for ua in (_USER_AGENTS[0], _BROWSER_UA):
        headers = {
            "User-Agent": ua,
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=10.0) as client:
                r = await client.get(embed_url)
                r.raise_for_status()
                soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)

                # When the embedded post is a reply, Threads renders the parent
                # post it's replying to *and* the reply itself as two full post
                # blocks on the same page. Scope every lookup to the block
                # carrying "OuterContainerFull" (the actual target of the embed
                # URL) so we don't pick up the parent post's image/caption/time.
                post_scope = soup.find(
                    lambda t: t.name == "div" and t.get("class") and "OuterContainerFull" in t.get("class")
                ) or soup

                media_container = post_scope.find(class_="MediaScrollContainer") or post_scope.find(class_="SoloMediaContainer")
                images = []
                video = None
                if media_container:
                    video_tag = media_container.find("video")
                    if video_tag:
                        source_tag = video_tag.find("source")
                        if source_tag and source_tag.get("src"):
                            video = source_tag["src"]
                    for img in media_container.find_all("img"):
                        src = img.get("src")
                        if src:
                            images.append(src)

                taken_at = None
                ts_tag = post_scope.find(class_="Timestamp")
                if ts_tag:
                    dt = _parse_threads_timestamp(ts_tag.get_text())
                    if dt:
                        taken_at = int(dt.timestamp())

                caption = None
                body_tag = post_scope.find(class_="BodyTextContainer")
                if body_tag:
                    caption = body_tag.get_text("\n", strip=True)

                avatar = None
                avatar_container = post_scope.find(class_="AvatarContainer")
                if avatar_container:
                    avatar_img = avatar_container.find("img")
                    if avatar_img and avatar_img.get("src"):
                        avatar = avatar_img["src"]

                # A post that rendered (we found its author/timestamp chrome) but
                # has no media container and no video is a genuine text-only post.
                # Its og:image on the regular post page is Meta's auto-generated
                # "text card" thumbnail, which has a faded/cut-off look rather
                # than being a real photo — callers should fall back to the
                # author's small avatar instead, like the old summary-card look.
                rendered = ts_tag is not None or post_scope.find(class_="AuthorIdentity") is not None
                if rendered:
                    return {
                        "images": images[:4],
                        "video": video,
                        "taken_at": taken_at,
                        "caption": caption,
                        "avatar": avatar,
                        "has_media": media_container is not None or video is not None,
                    }
        except Exception:
            continue
    return None


# Instagram's media ids are Snowflake-style: the top 41 bits are the post time
# in milliseconds since Instagram's own epoch (2011-08-24 21:07:01.721 UTC).
# Meta stopped shipping "taken_at_timestamp" in the embed page's JSON, so this
# is the only source left for an Instagram post's time — and it's exact.
_IG_ID_EPOCH_MS = 1314220021721
_IG_ID_TIME_SHIFT = 23
_IG_SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _ig_media_id_to_taken_at(media_id):
    """Recover a post's epoch seconds from its numeric media id."""
    try:
        ms = (int(media_id) >> _IG_ID_TIME_SHIFT) + _IG_ID_EPOCH_MS
    except (TypeError, ValueError):
        return None
    taken_at = ms // 1000
    # Reject anything outside Instagram's lifetime — a malformed id would
    # otherwise decode into a plausible-looking but wrong timestamp.
    now = int(datetime.now(tz=timezone.utc).timestamp())
    if not (1262304000 <= taken_at <= now + 86400):  # 2010-01-01 .. tomorrow
        return None
    return taken_at


def _ig_shortcode_taken_at(url: str):
    """Same trick straight from the URL: an Instagram shortcode is just the
    media id in base64, so /p/<code>/ alone is enough to date the post even
    when the embed page gives us nothing."""
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]+)", url or "")
    if not m:
        return None
    media_id = 0
    for ch in m.group(1):
        idx = _IG_SHORTCODE_ALPHABET.find(ch)
        if idx < 0:
            return None
        media_id = media_id * 64 + idx
    return _ig_media_id_to_taken_at(media_id)


def _extract_ig_context_json(html_text: str):
    """Pull the gql_data.shortcode_media object out of an Instagram embed page.
    It ships as a JSON-encoded string value (contextJSON) inside one of the
    page's bootloader script blobs, so it needs a second json.loads pass."""
    m = re.search(r'"contextJSON":"((?:[^"\\]|\\.)*)"', html_text)
    if not m:
        return None
    try:
        ctx_str = json.loads('"' + m.group(1) + '"')
        ctx = json.loads(ctx_str)
        return ctx.get("gql_data", {}).get("shortcode_media")
    except Exception:
        return None


async def _fetch_ig_embed_extra(embed_url: str):
    """Instagram's regular post page also stopped shipping carousel/media JSON,
    so full sidecar images and video are scraped from the /embed/captioned/
    widget page instead, which still embeds the full GraphQL media object."""
    headers = {
        "User-Agent": _USER_AGENTS[0],
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=10.0) as client:
            r = await client.get(embed_url)
            r.raise_for_status()
            media = _extract_ig_context_json(r.text)
            if not media:
                return None

            images = []
            video = None
            sidecar = media.get("edge_sidecar_to_children")
            if sidecar:
                for edge in sidecar.get("edges", []):
                    node = edge.get("node", {})
                    if node.get("is_video"):
                        if not video and node.get("video_url"):
                            video = node["video_url"]
                    elif node.get("display_url"):
                        images.append(node["display_url"])
            elif media.get("is_video"):
                video = media.get("video_url") or None
            elif media.get("display_url"):
                images.append(media["display_url"])

            caption = None
            cap_edges = media.get("edge_media_to_caption", {}).get("edges", [])
            if cap_edges:
                caption = cap_edges[0].get("node", {}).get("text")

            return {
                "images": images[:4],
                "video": video,
                "taken_at": _ig_media_id_to_taken_at(media.get("id")),
                "caption": caption,
            }
    except Exception:
        return None


def _cdn_asset_id(url: str):
    """Extract the stable filename component (e.g. '357932799_255309680558991_
    6705625412184876886_n.jpg') from a Meta CDN image URL, ignoring the query
    string (which carries resize params like stp=dst-jpg_s640x640) and host/
    bucket prefix. Two URLs with the same asset id are the same underlying
    image at a different resolution."""
    if not url:
        return None
    m = re.search(r"/([^/?]+_n\.\w+)(?:\?|$)", url)
    return m.group(1) if m else None


async def _fetch_threads_video_poster(resolved_url: str, avatar_url: str = None):
    """Threads' /embed page gives no poster for video posts (only the raw
    <video><source> mp4), and the regular post page's og:image (fetched with
    the UAs known chat-app crawlers use) is Meta's auto-generated "share card"
    with the username/caption baked in. Refetching the regular post page with
    a search-engine-style UA (Googlebot/curl) instead usually returns a real,
    clean video cover frame from the actual media CDN (t51.*) rather than the
    card generator (t39.*) — presumably because Meta only stylizes the crawler
    UAs chat apps use for their own link-preview branding.

    For some posts (e.g. ones cross-posted from an Instagram video/Reel),
    Threads has no distinct cover frame to serve and the Googlebot og:image
    falls back to the author's own profile picture instead — which still
    lives under a "/t51." path, so the naive CDN-bucket check alone can't
    tell it apart from a real cover frame. Compare against the already-known
    avatar URL (scraped from the /embed page) and reject a "poster" that's
    actually just the avatar at a different resolution."""
    headers = {
        "User-Agent": _USER_AGENTS[2],  # Googlebot
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=10.0) as client:
            r = await client.get(resolved_url)
            r.raise_for_status()
            soup = BeautifulSoup(r.content, "html.parser", from_encoding=r.encoding)
            tag = soup.find("meta", {"property": "og:image"})
            image = tag["content"] if tag and tag.get("content") else None
            if image and "/t51." in image and _cdn_asset_id(image) != _cdn_asset_id(avatar_url):
                return image
    except Exception:
        pass
    return None


# How much of the mp4 to pull when decoding a cover frame ourselves. Meta's
# CDN serves faststart-style files (moov atom up front), so the first frame
# decodes from a small prefix — 128KB sufficed in testing; 512KB leaves
# headroom for higher-bitrate clips while still being a fraction of the file.
_COVER_PROBE_BYTES = 512 * 1024


# Play badge geometry, as fractions of the frame's short side / the badge box.
_BADGE_SCALE = 0.20
_BADGE_SUPERSAMPLE = 4  # draw oversized, then downscale, so edges come out smooth


def _overlay_play_button(jpeg_bytes: bytes):
    """Stamp a play badge over a cover frame we decoded ourselves.

    Such a frame is otherwise indistinguishable from an ordinary photo post,
    so the badge is what tells the reader there's a video behind the link —
    the same affordance a native video embed gives. Failures here are not
    worth losing the frame over, so the original bytes are returned as-is."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return jpeg_bytes
    try:
        im = Image.open(io.BytesIO(jpeg_bytes)).convert("RGB")
        w, h = im.size
        size = max(48, int(min(w, h) * _BADGE_SCALE))
        ss = size * _BADGE_SUPERSAMPLE

        badge = Image.new("RGBA", (ss, ss), (0, 0, 0, 0))
        draw = ImageDraw.Draw(badge)
        draw.ellipse((0, 0, ss - 1, ss - 1), fill=(0, 0, 0, 140))
        # A triangle's optical centre sits left of its bounding box, so nudge
        # it right to keep it looking centred inside the circle.
        tw, th = ss * 0.32, ss * 0.36
        cx, cy = ss / 2 + ss * 0.03, ss / 2
        draw.polygon(
            [(cx - tw / 2, cy - th / 2), (cx - tw / 2, cy + th / 2), (cx + tw / 2, cy)],
            fill=(255, 255, 255, 240),
        )

        badge = badge.resize((size, size), Image.LANCZOS)
        im.paste(badge, ((w - size) // 2, (h - size) // 2), badge)

        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=88)
        return buf.getvalue()
    except Exception as e:
        print(f"[scraper] play badge overlay failed: {e}")
        return jpeg_bytes


def _ffmpeg_exe():
    """Path to the ffmpeg binary. imageio-ffmpeg bundles a static build, which
    keeps the image ~360MB smaller than installing Debian's ffmpeg package;
    fall back to whatever is on PATH so a dev box without it still works."""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


async def _extract_video_cover(video_url: str):
    """Decode the video's own first frame into JPEG bytes.

    Some Threads posts — notably ones cross-posted from an Instagram video or
    Reel — have no cover frame anywhere in Meta's markup: every crawler UA
    returns either the auto-generated share card or the author's avatar, and
    the /embed page's <video> tag carries no poster attribute. Decoding the
    frame locally is the only way to show what the video actually contains.

    Only the head of the file is fetched (the CDN honours Range requests) and
    ffmpeg reads it straight from stdin, so this needs no temp files and costs
    a fraction of a full download."""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            r = await client.get(video_url, headers={"Range": f"bytes=0-{_COVER_PROBE_BYTES - 1}"})
            r.raise_for_status()
            chunk = r.content

        proc = await asyncio.create_subprocess_exec(
            _ffmpeg_exe(), "-v", "error", "-i", "pipe:0",
            "-frames:v", "1", "-q:v", "3", "-f", "image2", "-vcodec", "mjpeg", "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # ffmpeg stops reading as soon as it has the frame, so the unread tail
        # of the chunk can break the pipe — communicate() swallows that.
        stdout, stderr = await proc.communicate(chunk)
        if proc.returncode == 0 and stdout:
            return _overlay_play_button(stdout)
        print(f"[scraper] cover frame extraction failed (rc={proc.returncode}): "
              f"{stderr[:200].decode(errors='replace')}")
    except Exception as e:
        print(f"[scraper] cover frame extraction failed: {e}")
    return None


async def fetch_metadata(url: str, max_retries: int = None):
    """
    Fetches OpenGraph metadata from a Threads or Instagram URL with retry logic.
    On login wall, rotates User-Agent across retries.
    """
    if max_retries is None:
        max_retries = len(_USER_AGENTS)

    # 嘗試序列：有 cookies 就先用 cookie 嘗試，再 fallback 到 UA 輪替
    attempts = []
    if _COOKIES:
        attempts.append((_BROWSER_UA, _COOKIES))
    for ua in _USER_AGENTS[:max_retries]:
        attempts.append((ua, None))

    for attempt_idx, (ua, cookies) in enumerate(attempts):
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, cookies=cookies or {}, timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                # Use response.content and let BeautifulSoup handle encoding or detect it from headers
                content = response.content
                soup = BeautifulSoup(content, 'html.parser', from_encoding=response.encoding)
                
                metadata = {
                    "title": None,
                    "description": None,
                    "image": None,
                    "images": [],
                    "video": None,
                    "type": None,
                    "card": None,
                    "url": str(response.url)
                }
                
                # Extract basic OG and Twitter tags
                def get_meta(soup, attrs):
                    for attr in attrs:
                        tag = soup.find("meta", attr)
                        if tag and tag.get("content"):
                            return tag["content"]
                    return None

                metadata["title"] = get_meta(soup, [{"property": "og:title"}, {"name": "og:title"}, {"name": "twitter:title"}])
                metadata["description"] = get_meta(soup, [{"property": "og:description"}, {"name": "og:description"}, {"name": "twitter:description"}])
                metadata["image"] = get_meta(soup, [{"property": "og:image"}, {"name": "og:image"}, {"name": "twitter:image"}])
                metadata["video"] = get_meta(soup, [{"property": "og:video"}, {"name": "og:video"}])
                metadata["type"] = get_meta(soup, [{"property": "og:type"}, {"name": "og:type"}])
                metadata["card"] = get_meta(soup, [{"name": "twitter:card"}])

                # Fallback for description if og:description is missing
                if not metadata["description"]:
                    metadata["description"] = get_meta(soup, [{"name": "twitter:description"}])

                # Fallback for title if og:title is missing
                if not metadata["title"] and soup.title:
                    metadata["title"] = soup.title.string

                metadata["taken_at"] = None

                # Detect login wall (Threads redirected to a sign-in page)
                desc = metadata.get("description")
                title = metadata.get("title") or ""
                
                is_login_wall = (
                    (desc and "Join Threads" in desc)
                    or (desc and "加入 Threads" in desc)
                    or ("Join Threads" in response.text and not desc)
                    or ("login" in str(response.url).lower())
                    or (title.startswith("Threads • Log in"))
                    or (title.startswith("Threads • 登入"))
                )

                # Detect a blocked/JS-only shell page: title fell back to the bare
                # site name (no real og:title) and there's no description or image,
                # meaning we didn't actually get the post's content.
                is_empty_shell = (
                    title in ("Threads", "Instagram")
                    and not desc
                    and not metadata.get("image")
                )

                if is_login_wall or is_empty_shell:
                    remaining = len(attempts) - attempt_idx - 1
                    if remaining > 0:
                        next_ua, _ = attempts[attempt_idx + 1]
                        mode = "cookie" if cookies else "UA"
                        reason = "login page" if is_login_wall else "empty shell page"
                        print(f"Got {reason} for {url} ({mode}: {ua[:40]}). Retrying with '{next_ua[:40]}'... ({attempt_idx + 1}/{len(attempts)})")
                        continue
                    else:
                        reason = "login page" if is_login_wall else "empty shell page"
                        print(f"Got {reason} for {url}. All {len(attempts)} attempts exhausted.")
                        return None

                # The regular post page no longer ships carousel/media JSON in its
                # SSR HTML (Meta removed it), so fetch the official embed widget
                # page for full-resolution images/video, caption and timestamp.
                resolved_url = str(response.url).split("?")[0].rstrip("/")
                is_instagram = "instagram.com" in resolved_url
                if is_instagram:
                    metadata["taken_at"] = _ig_shortcode_taken_at(resolved_url)
                try:
                    if is_instagram:
                        extra = await _fetch_ig_embed_extra(resolved_url + "/embed/captioned/")
                    else:
                        extra = await _fetch_threads_embed_extra(resolved_url + "/embed")
                except Exception as e:
                    print(f"[scraper] embed enrichment failed for {url}: {e}")
                    extra = None

                if extra:
                    if extra.get("images"):
                        metadata["images"] = extra["images"]
                    if extra.get("video") and not metadata.get("video"):
                        metadata["video"] = extra["video"]
                    if extra.get("taken_at"):
                        metadata["taken_at"] = extra["taken_at"]
                    if extra.get("caption") and not metadata.get("description"):
                        metadata["description"] = extra["caption"]
                    if not is_instagram and extra.get("has_media") is False:
                        # Confirmed text-only post: the og:image we fell back to
                        # is Meta's auto-generated "text card", not a real photo.
                        # Show the author's avatar as a small thumbnail instead,
                        # matching the old summary-card look. Force card back to
                        # "summary" so bot.py's size logic picks set_thumbnail
                        # rather than treating this as a large media post.
                        metadata["image"] = extra.get("avatar")
                        metadata["card"] = "summary"
                    elif not is_instagram and extra.get("video") and not extra.get("images"):
                        # Video post: the og:image we fell back to is Meta's
                        # auto-generated share card (username/caption baked in,
                        # faded look), not the video's own cover frame. Try to
                        # replace it with a real, clean cover frame.
                        poster = await _fetch_threads_video_poster(resolved_url, extra.get("avatar"))
                        if poster:
                            metadata["image"] = poster
                        else:
                            # Meta ships no cover frame for this post at all,
                            # so decode the video's first frame ourselves and
                            # let the caller upload it alongside the embed.
                            metadata["cover_bytes"] = await _extract_video_cover(extra["video"])

                return metadata

        except Exception as e:
            remaining = len(attempts) - attempt_idx - 1
            if remaining > 0:
                print(f"Error fetching metadata for {url}: {e}. Retrying with next attempt... ({attempt_idx + 1}/{len(attempts)})")
                await asyncio.sleep(2)
            else:
                print(f"Error fetching metadata for {url}: {e}. Max retries reached.")
                return None


if __name__ == "__main__":
    import asyncio
    
    async def test():
        url = "https://www.threads.com/@inoyuzu_skz46/post/DV280RrgfPl"
        data = await fetch_metadata(url)
        print(data)
        
    asyncio.run(test())
