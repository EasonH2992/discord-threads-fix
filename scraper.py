import httpx
from bs4 import BeautifulSoup
import re
import asyncio

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

async def fetch_metadata(url: str, max_retries: int = None):
    """
    Fetches OpenGraph metadata from a Threads or Instagram URL with retry logic.
    On login wall, rotates User-Agent across retries.
    """
    if max_retries is None:
        max_retries = len(_USER_AGENTS)

    for attempt in range(max_retries):
        ua = _USER_AGENTS[attempt % len(_USER_AGENTS)]
        headers = {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        try:
            async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=10.0) as client:
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

                # Try to extract the timestamp from the JSON blobs
                match = re.search(r'"taken_at":(\d+)', response.text)
                if match:
                    metadata["taken_at"] = int(match.group(1))
                else:
                    metadata["taken_at"] = None

                # Try to extract carousel images (multi-image posts)
                import json
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
                        for item in carousel[:4]:
                            candidates = item.get("image_versions2", {}).get("candidates", [])
                            if candidates:
                                metadata["images"].append(candidates[0]["url"])
                    except Exception:
                        pass

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
                
                if is_login_wall:
                    if attempt < max_retries - 1:
                        next_ua = _USER_AGENTS[(attempt + 1) % len(_USER_AGENTS)]
                        print(f"Got login page for {url} with UA '{ua}'. Retrying with UA '{next_ua}'... ({attempt + 1}/{max_retries})")
                        continue
                    else:
                        print(f"Got login page for {url}. All {max_retries} UAs exhausted.")
                        return None

                return metadata
                
        except Exception as e:
            if attempt < max_retries - 1:
                print(f"Error fetching metadata for {url}: {e}. Retrying with next UA... ({attempt + 1}/{max_retries})")
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
