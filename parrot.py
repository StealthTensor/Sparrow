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
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, DownloadColumn, TransferSpeedColumn
from magnet2torrent import Magnet2Torrent, FailedToFetchException

console = Console()
BASE_URL = "https://fitgirl-repacks.site"

custom_style = questionary.Style([
    ('qmark', 'fg:#f44336 bold'),
    ('question', 'bold'),
    ('pointer', 'fg:#673ab7 bold'),
    ('highlighted', 'fg:#673ab7 bold'),
    ('selected', 'fg:white bg:#673ab7'),
    ('answer', 'fg:#f44336 bold'),
])

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

async def extract_direct_download_url(client: httpx.AsyncClient, landing_url: str) -> str | None:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
        "Referer": "https://fitgirl-repacks.site/"
    }
    response = await client.get(landing_url, headers=headers)
    soup = BeautifulSoup(response.text, 'html.parser')

    form = soup.find('form')
    if not form:
        for a in soup.find_all('a', href=True):
            href = a['href']
            if any(ext in href.lower() for ext in ['.rar', '.zip', '.7z', '.bin']):
                return href
        return landing_url

    form_data = {}
    for input_tag in form.find_all('input'):
        name = input_tag.get('name')
        value = input_tag.get('value', '')
        if name:
            form_data[name] = value

    action_url = form.get('action', landing_url)

    post_headers = headers.copy()
    post_headers["Origin"] = "https://fuckingfast.co"
    post_headers["Content-Type"] = "application/x-www-form-urlencoded"

    post_response = await client.post(action_url, data=form_data, headers=post_headers, follow_redirects=False)

    if "Location" in post_response.headers:
        return post_response.headers["Location"]

    next_soup = BeautifulSoup(post_response.text, 'html.parser')
    btn = next_soup.find('a', class_='btn-download')
    if btn and btn.get('href'):
        return btn.get('href')

    for a in next_soup.find_all('a', href=True):
        href = a['href']
        if any(ext in href.lower() for ext in ['.rar', '.zip', '.7z', '.bin']):
            return href

    return landing_url

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

async def download_file(client: httpx.AsyncClient, filename: str, landing_url: str, downloads_dir: str = "./Downloads"):
    if not os.path.exists(downloads_dir):
        os.makedirs(downloads_dir)

    output_path = os.path.join(downloads_dir, filename)

    try:
        direct_url = await extract_direct_download_url(client, landing_url)

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0",
            "Referer": landing_url
        }

        async with client.stream("GET", direct_url, headers=headers, follow_redirects=True) as response:
            response.raise_for_status()
            total_size = int(response.headers.get("content-length", 0))

            with Progress(
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=20),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeElapsedColumn(),
                console=console
            ) as progress:
                task = progress.add_task(f"📥 {filename[:30]}", total=total_size)

                with open(output_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=16384):
                        f.write(chunk)
                        progress.update(task, advance=len(chunk))

    except Exception as e:
        console.print(f"[bold red]❌ Error downloading {filename}: {e}[/bold red]")

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
            title=f"{group} ({len(items)} {'file' if len(items) == 1 else 'files'}, {estimate_size(len(items))})",
            value=group,
            checked=(group == "Core Game Files (Required)")
        )
        for group, items in grouped_links.items()
    ]

    selected_groups = await questionary.checkbox(
        "Select the loot you want to haul (Space to toggle, Enter to confirm):",
        choices=choices,
        style=custom_style
    ).ask_async()

    if not selected_groups:
        console.print("[yellow]No loot selected. Returning to base.")
        return

    download_queue = []
    for group in selected_groups:
        download_queue.extend(grouped_links[group])

    file_label = "file" if len(download_queue) == 1 else "files"
    console.print(f"\n[bold green]Sparrow has queued {len(download_queue)} {file_label} ({estimate_size(len(download_queue))}) for execution![/bold green]")

    confirm = await questionary.confirm(
        "Do you want to begin downloading now?",
        default=True,
        style=custom_style
    ).ask_async()

    if not confirm:
        console.print("[yellow]Download aborted. Loot stays in queue.[/yellow]")
        return

    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        for filename, landing_url in download_queue:
            await download_file(client, filename, landing_url)

    console.print("\n[bold green]🏁 All selected files have been successfully processed![/bold green]")

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
        style=custom_style,
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
        style=custom_style
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
        "What are we hunting for today?",
        style=custom_style
    ).ask_async()

    if query:
        await search_flow(query)

if __name__ == "__main__":
    asyncio.run(main())
