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

                # Try to extract carousel images (multi-image posts).
                # Only attempt when og:image is an actual post image (summary_large_image),
                # not a profile picture (summary). Text-only posts use Card=summary and their
                # og:image is the poster's profile pic; any carousel_media found in the HTML
                # would belong to a different post embedded on the page.
                import json
                if metadata.get("card") == "summary_large_image" and metadata.get("image"):
                    idx = response.text.find('"carousel_media":[')
                    if idx != -1:
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
                            images = []
                            for item in carousel[:4]:
                                candidates = item.get("image_versions2", {}).get("candidates", [])
                                if candidates:
                                    images.append(candidates[0]["url"])
                            # Validate that this carousel belongs to the target post by checking
                            # whether the og:image numeric media-file ID (e.g. "724640022" in
                            # "724640022_17945406252200696_..._n.jpg") appears in any carousel
                            # item. Threads sometimes uses a non-first item (e.g. video cover
                            # frame) as og:image, so we check all items, not just carousel[0].
                            # No match means the carousel_media block came from a different post.
                            if images:
                                og_id = re.search(r'/(\d{9,})_\d+_\d+_n\.', metadata["image"])
                                if og_id:
                                    carousel_ids = [
                                        m.group(1) for url in images
                                        if (m := re.search(r'/(\d{9,})_\d+_\d+_n\.', url))
                                    ]
                                    if og_id.group(1) not in carousel_ids:
                                        images = []
                            metadata["images"] = images
                        except Exception:
                            pass

                # Detect login wall (Threads redirected to a sign-in page)
                is_login_wall = (
                    (metadata.get("description") and metadata["description"].startswith("Join Threads to share ideas"))
                    or ("Join Threads" in response.text and not metadata.get("description"))
                )
                if is_login_wall:
                    if attempt < max_retries - 1:
                        ua_next = _USER_AGENTS[(attempt + 1) % len(_USER_AGENTS)]
                        print(f"Got login page for {url}. Retrying with UA '{ua_next}'... ({attempt + 1}/{max_retries})")
                        await asyncio.sleep(5)
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
