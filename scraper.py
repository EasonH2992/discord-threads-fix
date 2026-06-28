import httpx
from bs4 import BeautifulSoup
import re
import asyncio
import json
import os

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

                # Try to extract the timestamp from the JSON blobs
                match = re.search(r'"taken_at":(\d+)', response.text)
                if match:
                    metadata["taken_at"] = int(match.group(1))
                else:
                    metadata["taken_at"] = None

                # Try to extract carousel images (multi-image posts)
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
                    remaining = len(attempts) - attempt_idx - 1
                    if remaining > 0:
                        next_ua, _ = attempts[attempt_idx + 1]
                        mode = "cookie" if cookies else "UA"
                        print(f"Got login page for {url} ({mode}: {ua[:40]}). Retrying with '{next_ua[:40]}'... ({attempt_idx + 1}/{len(attempts)})")
                        continue
                    else:
                        print(f"Got login page for {url}. All {len(attempts)} attempts exhausted.")
                        return None

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
