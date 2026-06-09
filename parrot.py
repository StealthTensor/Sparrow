import asyncio
import os
import sys
import subprocess
import platform
from dataclasses import dataclass
import httpx
from bs4 import BeautifulSoup
import questionary
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
from magnet2torrent import Magnet2Torrent, FailedToFetchException

console = Console()
BASE_URL = "https://fitgirl-repacks.site"

@dataclass
class SearchResult:
    title: str
    url: str

@dataclass
class SearchResults:
    results: list[SearchResult]
    previous_page: bool
    next_page: bool

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

def parse_game_page_for_magnet(html: str) -> str | None:
    soup = BeautifulSoup(html, 'html.parser')
    for a in soup.select(".entry-content ul li a"):
        href = a.get("href")
        if href and "magnet" in href:
            return href
    return None

def parse_ff_links(html: str) -> list:
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

async def search_fitgirl(query: str, page_number: int = 1) -> SearchResults | None:
    query = query.replace(" ", "+").strip().lower()
    url = f"{BASE_URL}/page/{page_number}/?s={query}"

    try:
        html = await fetch_html(url)
    except Exception:
        return None

    soup = BeautifulSoup(html, 'html.parser')
    articles = soup.select("article")
    pages = soup.select(".page-numbers")

    next_page = False
    previous_page = False

    for page in pages:
        if "next" in page.text.lower():
            next_page = True
        if "previous" in page.text.lower():
            previous_page = True

    results = []
    if not articles:
        return None

    for result in articles:
        try:
            a_tag = result.select_one("h1.entry-title a")
            if a_tag:
                title = a_tag.text
                target_url = a_tag.get("href")
                results.append(SearchResult(title=title, url=target_url))
        except Exception:
            continue

    return SearchResults(results, previous_page, next_page)

async def convert_magnet_to_torrent_file(magnet_uri: str, output_dir: str = "./torrent_cache") -> str | None:
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    m2t = Magnet2Torrent(magnet_uri)
    console.print(f"\n[bold yellow]🔍 Searching swarm for torrent metadata...[/bold yellow]")

    with Progress(
        SpinnerColumn(),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("[bold cyan]{task.description}"),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Connecting to peers...", total=100)
        try:
            retrieve_task = asyncio.create_task(m2t.retrieve_torrent())
            pct = 0
            while not retrieve_task.done():
                await asyncio.sleep(0.2)
                if pct < 90:
                    pct += 1
                    progress.update(task, completed=pct)

            filename, torrent_data = await retrieve_task
            progress.update(task, completed=100, description="Metadata retrieved!")

            safe_filename = filename if filename else m2t.info_hash
            final_output_path = os.path.join(output_dir, f"{safe_filename}.torrent")

            with open(final_output_path, "wb") as f:
                f.write(torrent_data)

            return final_output_path
        except FailedToFetchException:
            progress.stop()
            console.print("\n[bold red]❌ Failed to retrieve metadata.[/bold red] Peer discovery timed out.")
            return None
        except Exception as e:
            progress.stop()
            console.print(f"\n[bold red]❌ An unexpected error occurred:[/bold red] {e}")
            return None

def start_torrent_download(torrent_path: str):
    system = platform.system()
    console.print(f"\n[bold cyan]Attempting to launch client on {system}...[/bold cyan]")

    if system == "Windows":
        os.startfile(torrent_path)
    elif system == "Darwin":
        subprocess.run(["open", torrent_path], check=True)
    elif system == "Linux":
        subprocess.run(["xdg-open", torrent_path], check=True)
    else:
        console.print("[bold red]Unsupported operating system.[/bold red]")
        return

    console.print(
        Panel(
            f"[bold green]Download Initiated![/bold green]\n"
            f"File: [dim]{os.path.basename(torrent_path)}[/dim]\n\n"
            f"Check your default torrent client to view the progress.",
            border_style="green"
        )
    )

async def handle_direct_download(html: str):
    links = parse_ff_links(html)
    if not links:
        console.print("[bold red]No FuckingFast links found for this title.[/bold red]")
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

async def search_flow(query: str, page: int = 1):
    with console.status(f"[bold green]Searching for '{query}'...", spinner="dots"):
        data = await search_fitgirl(query, page)

    if not data or not data.results:
        console.print("[bold red]No results found.[/bold red]")
        return

    choices = [result.title for result in data.results]
    if data.previous_page:
        choices.insert(0, "⬅ Previous Page")
    if data.next_page:
        choices.append("➡ Next Page")

    selection = await questionary.select(
        "Select a game:",
        choices=choices,
        style=questionary.Style([('qmark', 'fg:cyan bold'), ('pointer', 'fg:green bold')]),
        qmark="\n🎮",
        pointer="➤"
    ).ask_async()

    if selection == "⬅ Previous Page":
        await search_flow(query, page - 1)
        return
    if selection == "➡ Next Page":
        await search_flow(query, page + 1)
        return

    selected_result = next((r for r in data.results if r.title == selection), None)
    if not selected_result:
        return

    method = await questionary.select(
        "How do you want to secure the loot?",
        choices=["Torrent (Magnet Link)", "Direct Download (FuckingFast Links)"],
        style=questionary.Style([('pointer', 'fg:green bold')])
    ).ask_async()

    with console.status("[bold green]Scouting the target page...", spinner="dots"):
        try:
            html = await fetch_html(selected_result.url)
        except Exception as e:
            console.print(f"[bold red]Failed to access page:[/bold red] {e}")
            return

    if "Torrent" in method:
        magnet_uri = parse_game_page_for_magnet(html)
        if not magnet_uri:
            console.print("[bold red]❌ Could not find a magnet link on this page.[/bold red]")
            return

        torrent_path = await convert_magnet_to_torrent_file(magnet_uri)
        if torrent_path:
            try:
                start_torrent_download(torrent_path)
            except Exception as e:
                console.print(f"[bold red]❌ Cannot launch torrent client:[/bold red] {e}")
    else:
        await handle_direct_download(html)

async def main():
    banner = """[bold cyan]██████╗  █████╗ ██████╗ ██████╗  ██████╗ ████████╗
██╔══██╗██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝
██████╔╝███████║██████╔╝██████╔╝██║   ██║   ██║
██╔═══╝ ██╔══██║██╔══██╗██╔══██╗██║   ██║   ██║
██║     ██║  ██║██║  ██║██║  ██║╚██████╔╝   ██║
╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝[/bold cyan]"""
    console.print(banner)
    console.print("[white]The Loyal Loot Retriever\n")

    query = await questionary.text(
        "What are we hunting for today?:",
        style=questionary.Style([('qmark', 'fg:cyan bold')])
    ).ask_async()

    if query:
        await search_flow(query)

if __name__ == "__main__":
    asyncio.run(main())
