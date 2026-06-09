import asyncio
import httpx
from bs4 import BeautifulSoup
import questionary
from rich.console import Console
from rich.panel import Panel

console = Console()

def estimate_size(num_files: int) -> str:
    if num_files == 0:
        return "0 MB"
    min_mb = (num_files - 1) * 500
    max_mb = num_files * 500
    avg_mb = (min_mb + max_mb) / 2
    if avg_mb >= 1024:
        return f"~{avg_mb / 1024:.2f} GB"
    return f"~{avg_mb:.0f} MB"

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

def group_files(links: list) -> dict:
    groups = {"Core Game Files (Required)": []}
    for name, url in links:
        if name.startswith("fg-optional-"):
            category = name.split('.')[0].replace("fg-optional-", "").replace("-", " ").title()
            group_name = f"Optional: {category}"
            if group_name not in groups:
                groups[group_name] = []
            groups[group_name].append((name, url))
        else:
            groups["Core Game Files (Required)"].append((name, url))
    return groups

async def sparrow_interactive():
    console.print(Panel.fit("[bold cyan]☠️ SPARROW v1.0 ☠️\n[white]The Loyal Loot Retriever", border_style="cyan"))

    test_url = await questionary.text("Drop the target link:").ask_async()
    if not test_url:
        return

    with console.status("[bold green]Sparrow is scouting ahead...", spinner="dots"):
        try:
            html = await fetch_html(test_url)
            links = parse_links(html)
        except Exception as e:
            console.print(f"[bold red]Sparrow hit a storm: {e}")
            return

    grouped_links = group_files(links)

    choices = [
        questionary.Choice(
            title=f"{group} ({len(items)} files, {estimate_size(len(items))})",
            value=group,
            checked=(group == "Core Game Files (Required)")
        )
        for group, items in grouped_links.items()
    ]

    selected_groups = await questionary.checkbox(
        "Select the loot you want to haul (Space to toggle, Enter to confirm):",
        choices=choices,
        style=questionary.Style([('answer', 'fg:cyan bold'), ('pointer', 'fg:green bold')])
    ).ask_async()

    if not selected_groups:
        console.print("[yellow]No loot selected. Returning to base.")
        return

    download_queue = []
    for group in selected_groups:
        download_queue.extend(grouped_links[group])

    console.print(f"\n[bold green]Sparrow has queued {len(download_queue)} files ({estimate_size(len(download_queue))}) for the haul![/bold green]")
    for item in download_queue[:5]:
        console.print(f"  - {item[0]}")
    if len(download_queue) > 5:
        console.print(f"  ...and {len(download_queue) - 5} more.")

if __name__ == "__main__":
    asyncio.run(sparrow_interactive())
