import httpx
from bs4 import BeautifulSoup
import re

async def fetch_threads_metadata(url: str):
    """
    Fetches OpenGraph metadata from a Threads URL.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
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

            return metadata
            
    except Exception as e:
        print(f"Error fetching metadata for {url}: {e}")
        return None

if __name__ == "__main__":
    import asyncio
    
    async def test():
        url = "https://www.threads.com/@inoyuzu_skz46/post/DV280RrgfPl"
        data = await fetch_threads_metadata(url)
        print(data)
        
    asyncio.run(test())
