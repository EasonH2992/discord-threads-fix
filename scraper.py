import httpx
from bs4 import BeautifulSoup
import re
import asyncio

_USER_AGENTS = [
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
    "curl/8.7.1",
]

async def fetch_metadata(url: str, max_retries: int = 3):
    """
    Fetches OpenGraph metadata from a Threads or Instagram URL with retry logic.
    On login wall, rotates User-Agent across retries.
    """
    for attempt in range(max_retries):
        headers = {
            "User-Agent": _USER_AGENTS[attempt % len(_USER_AGENTS)],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=10.0) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                
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

                # Fallback for description if og:description is missing (often just the content of the post)
                if not metadata["description"]:
                    # Sometimes the description is in twitter:description
                    metadata["description"] = get_meta(soup, [{"name": "twitter:description"}])

                # Fallback for title if og:title is missing
                if not metadata["title"] and soup.title:
                    metadata["title"] = soup.title.string

                # Try to extract the timestamp from the JSON blobs
                # Look for "taken_at":1234567890
                match = re.search(r'"taken_at":(\d+)', response.text)
                if match:
                    metadata["taken_at"] = int(match.group(1))
                else:
                    metadata["taken_at"] = None

                # Extract reply context for Threads reply posts
                # reply_to_author gives us the username; thread_items[0] is the parent post
                metadata["reply_to"] = None
                rta_match = re.search(r'"reply_to_author":\{"[^}]*?"username":"([^"]+)"', response.text)
                if rta_match:
                    parent_username = rta_match.group(1)
                    parent_text = None
                    ti_idx = response.text.find('"thread_items":[')
                    if ti_idx != -1:
                        slice_ = response.text[ti_idx:ti_idx + 5000]
                        # 1. Try caption.text (plain text post)
                        cap_m = re.search(r'"caption":\{"text":"((?:[^"\\]|\\.)*)"', slice_)
                        if cap_m:
                            try:
                                import json as _json
                                parent_text = _json.loads('"' + cap_m.group(1) + '"')
                            except Exception:
                                parent_text = cap_m.group(1)
                        # 2. Fallback: link_preview title (reel / linked post)
                        if not parent_text:
                            lp_m = re.search(r'"link_preview_attachment":\{[^}]*?"title":"((?:[^"\\]|\\.)*)"', slice_)
                            if lp_m:
                                try:
                                    import json as _json
                                    parent_text = _json.loads('"' + lp_m.group(1) + '"')
                                except Exception:
                                    parent_text = lp_m.group(1)
                    metadata["reply_to"] = {"username": parent_username, "text": parent_text}

                # Try to extract carousel images (multi-image posts)
                # Extract media ID from og:image ig_cache_key to validate carousel ownership
                import json, base64, urllib.parse
                og_media_id = None
                if metadata["image"]:
                    ck_match = re.search(r'ig_cache_key=([^&]+)', metadata["image"])
                    if ck_match:
                        try:
                            og_media_id = base64.b64decode(
                                urllib.parse.unquote(ck_match.group(1)) + "=="
                            ).decode("ascii", errors="replace").strip("\x00").rstrip("?")
                        except Exception:
                            pass

                # Skip carousel extraction for Reels: og:image has no ig_cache_key (CLIPS urlgen)
                # and og:video is present, meaning this is a video post, not a photo carousel.
                idx = response.text.find('"carousel_media":[')
                if idx != -1 and og_media_id is not None:
                    start = idx + len('"carousel_media":')
                    depth, i = 0, start
                    while i < len(response.text):
                        if response.text[i] == '[': depth += 1
                        elif response.text[i] == ']':
                            depth -= 1
                            if depth == 0:
                                break
                        i += 1
                    try:
                        carousel = json.loads(response.text[start:i+1])
                        # Validate: first carousel item's media ID must match og:image media ID
                        valid = True
                        if og_media_id and carousel:
                            first_candidates = carousel[0].get("image_versions2", {}).get("candidates", [])
                            if first_candidates:
                                c1_ck = re.search(r'ig_cache_key=([^&]+)', first_candidates[0].get("url", ""))
                                if c1_ck:
                                    try:
                                        c1_id = base64.b64decode(
                                            urllib.parse.unquote(c1_ck.group(1)) + "=="
                                        ).decode("ascii", errors="replace").strip("\x00").rstrip("?")
                                        valid = (c1_id == og_media_id)
                                    except Exception:
                                        valid = False
                        if valid:
                            for item in carousel[:4]:
                                candidates = item.get("image_versions2", {}).get("candidates", [])
                                if candidates:
                                    metadata["images"].append(candidates[0]["url"])
                    except Exception:
                        pass

                # Detect login wall (Threads redirected to a sign-in page)
                if metadata.get("description") and metadata["description"].startswith("Join Threads to share ideas"):
                    if attempt < max_retries - 1:
                        ua_next = _USER_AGENTS[(attempt + 1) % len(_USER_AGENTS)]
                        print(f"Got login page for {url}. Retrying with UA '{ua_next}'... ({attempt + 1}/{max_retries})")
                        await asyncio.sleep(3)
                        continue
                    else:
                        print(f"Got login page for {url}. All {max_retries} UAs exhausted.")
                        return None

                return metadata
                
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Error fetching metadata for {url}: {e}. Retrying in 3 seconds... ({attempt + 1}/{max_retries})")
                await asyncio.sleep(3)
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
