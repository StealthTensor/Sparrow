import asyncio
import httpx
from bs4 import BeautifulSoup

async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }
    timeout = httpx.Timeout(25.0)
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text

def parse_links(html: str) -> list:
    soup = BeautifulSoup(html, 'html.parser')
    ff_links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'fuckingfast.co' in href:
            filename = a.get_text(strip=True)
            if filename:
                ff_links.append((filename, href))
    return ff_links

async def test_scraper():
    test_url = input("Drop the link: ")
    print("\nScouting ahead with updated browser headers...")
    try:
        html = await fetch_html(test_url)
        links = parse_links(html)
        for name, link in links:
            print(f"{name}  =>  {link}")
        print(f"\nTotal loot found: {len(links)}")
    except httpx.ConnectTimeout:
        print("\nConnection timed out again. The target endpoint appears to be strictly blocked on this network pathway.")
    except httpx.HTTPStatusError as e:
        print(f"\nServer block encountered. Status code: {e.response.status_code}")
    except Exception as e:
        print(f"\nUnexpected error encountered: {e}")

if __name__ == "__main__":
    asyncio.run(test_scraper())
