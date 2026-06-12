import click
from datetime import datetime, timedelta
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box
from rich.prompt import Prompt

from . import database
from . import rss_fetcher
from . import opml
from . import player
from . import stats
from .player import format_duration


console = Console()


def parse_progress_input(progress_str: str, total_duration: int = 0) -> int:
    progress_str = progress_str.strip()
    if not progress_str:
        return 0
    if progress_str.endswith("%"):
        try:
            pct = float(progress_str[:-1])
            if total_duration > 0:
                return int(total_duration * pct / 100)
            return 0
        except ValueError:
            return 0
    return player.parse_time_string(progress_str)


def resolve_episode_id(episode_query: str) -> int:
    try:
        eid = int(episode_query)
        ep = database.get_episode_by_id(eid)
        if ep:
            return eid
    except ValueError:
        pass

    matches = database.find_episode_by_query(episode_query)
    if not matches:
        console.print(f"[red]✗ 找不到匹配「{episode_query}」的节目[/red]")
        return -1

    if len(matches) == 1:
        ep = matches[0]
        console.print(f"[green]匹配到:[/green] [{ep['id']}] {ep['podcast_title']} - {ep['title']}")
        return ep["id"]

    console.print(f"[cyan]找到 {len(matches)} 个匹配:[/cyan]")
    for i, ep in enumerate(matches[:8], 1):
        duration = format_duration(ep["duration"]) if ep["duration"] else "?"
        if ep["is_listened"]:
            status = "[green]✓[/green]"
        elif ep["progress"] and ep["progress"] > 0:
            status = "[yellow]⏸[/yellow]"
        else:
            status = " "
        console.print(f"  {status} [{ep['id']}] {ep['podcast_title']} - {ep['title']} ({duration})")

    try:
        choice = Prompt.ask("请输入节目ID", console=console)
        eid = int(choice)
        ep = database.get_episode_by_id(eid)
        if ep:
            return eid
        console.print("[red]✗ 该ID不存在[/red]")
        return -1
    except (ValueError, EOFError, KeyboardInterrupt):
        console.print("[yellow]已取消[/yellow]")
        return -1


def validate_podcast_id(ctx, param, value):
    if value is None:
        return None
    try:
        pid = int(value)
        podcast = database.get_podcast_by_id(pid)
        if not podcast:
            raise click.BadParameter(f"播客 ID {pid} 不存在")
        return pid
    except ValueError:
        raise click.BadParameter("播客 ID 必须是数字")


def validate_episode_id(ctx, param, value):
    if value is None:
        return None
    try:
        eid = int(value)
        episode = database.get_episode_by_id(eid)
        if not episode:
            raise click.BadParameter(f"节目 ID {eid} 不存在")
        return eid
    except ValueError:
        raise click.BadParameter("节目 ID 必须是数字")


@click.group()
@click.version_option(version="1.0.0", prog_name="podcast")
def cli():
    """播客订阅源管理工具 - 在终端里管理你的播客"""
    database.init_db()


@cli.command()
def list():
    """列出所有订阅的播客"""
    podcasts_with_counts = database.get_podcast_with_episode_count()

    if not podcasts_with_counts:
        console.print("[yellow]还没有订阅任何播客[/yellow]")
        console.print("使用 [bold]podcast add <RSS地址>[/bold] 添加第一个播客吧！")
        return

    table = Table(title="我的播客订阅", box=box.ROUNDED)
    table.add_column("ID", style="cyan", justify="right", width=4)
    table.add_column("播客名称", style="bold green")
    table.add_column("节目数", justify="right", width=6)
    table.add_column("未听", justify="right", width=6)
    table.add_column("跳过率", justify="right", width=8)
    table.add_column("上次更新", style="dim", width=19)

    for podcast, total, unplayed in podcasts_with_counts:
        skip_rate = database.get_podcast_skip_rate(podcast["id"])
        last_updated = podcast["last_updated"]
        if last_updated:
            try:
                dt = datetime.fromisoformat(last_updated)
                last_updated_str = dt.strftime("%Y-%m-%d %H:%M")
            except ValueError:
                last_updated_str = "未知"
        else:
            last_updated_str = "从未"

        skip_rate_str = f"{skip_rate * 100:.1f}%"
        skip_style = "red" if skip_rate > 0.5 else "yellow" if skip_rate > 0.2 else "green"

        table.add_row(
            str(podcast["id"]),
            podcast["title"],
            str(total),
            f"[bold]{unplayed}[/bold]" if unplayed > 0 else str(unplayed),
            f"[{skip_style}]{skip_rate_str}[/{skip_style}]",
            last_updated_str,
        )

    console.print(table)


@cli.command()
@click.argument("feed_url")
def add(feed_url):
    """添加一个播客订阅（RSS地址）"""
    with console.status("[bold green]正在抓取播客源..."):
        try:
            podcast_id, new_count = rss_fetcher.add_podcast_from_url(feed_url)
            podcast = database.get_podcast_by_id(podcast_id)
            console.print()
            console.print(f"[green]✓ 成功添加播客:[/green] [bold]{podcast['title']}[/bold]")
            console.print(f"  共获取 {new_count} 集节目")
            console.print(f"  RSS地址: {feed_url}")
        except Exception as e:
            console.print(f"[red]✗ 添加失败: {e}[/red]")


@cli.command()
@click.argument("podcast_id", callback=validate_podcast_id, required=False)
@click.option("--all", "-a", is_flag=True, help="刷新所有播客")
@click.option("--recent", "-r", is_flag=True, help="刷新后显示最近新增的未听节目")
@click.option("--days", "-d", type=int, default=7, help="显示最近几天的节目(配合--recent)")
def refresh(podcast_id, all, recent, days):
    """刷新播客，获取最新节目"""
    if all:
        with console.status("[bold green]正在刷新所有播客..."):
            success, new_count, results = rss_fetcher.refresh_all_podcasts()

        console.print()
        console.print(f"[green]✓ 刷新了 {success} 个播客，新增 {new_count} 集节目[/green]")

        console.print()
        table = Table(title="刷新详情", box=box.ROUNDED)
        table.add_column("状态", justify="center", width=5)
        table.add_column("播客名称", style="bold")
        table.add_column("新增", justify="right", width=5)
        table.add_column("详情", style="dim")

        for r in results:
            if r["success"]:
                icon = "[green]✓[/green]"
                detail = ", ".join(r["new_titles"][:3]) if r["new_titles"] else "无新增"
                if len(r["new_titles"]) > 3:
                    detail += f" 等{len(r['new_titles'])}集"
            else:
                icon = "[red]✗[/red]"
                detail = r["error"] or "未知错误"
            table.add_row(icon, r["title"], str(r["new_count"]) if r["success"] else "-", detail)

        console.print(table)

        if recent:
            console.print()
            recent_eps = database.get_recent_episodes(days=days, only_unplayed=True)
            if recent_eps:
                console.print(f"[bold cyan]近 {days} 天新增未听节目 ({len(recent_eps)} 集):[/bold cyan]")
                for ep in recent_eps[:15]:
                    duration = format_duration(ep["duration"]) if ep["duration"] else "?"
                    pub_date = ep["pub_date"][:10] if ep["pub_date"] else "?"
                    console.print(f"  [{ep['id']}] {ep['podcast_title']} - {ep['title']} [dim]({duration} · {pub_date})[/dim]")
                if len(recent_eps) > 15:
                    console.print(f"  [dim]... 还有 {len(recent_eps) - 15} 集[/dim]")
            else:
                console.print(f"[dim]近 {days} 天没有新增未听节目[/dim]")

    elif podcast_id:
        podcast = database.get_podcast_by_id(podcast_id)
        with console.status(f"[bold green]正在刷新 {podcast['title']}..."):
            try:
                new_count, new_titles = rss_fetcher.refresh_podcast(podcast_id)
                console.print(f"[green]✓ 刷新完成，新增 {new_count} 集节目[/green]")
                if new_titles:
                    for t in new_titles[:5]:
                        console.print(f"  [dim]+ {t}[/dim]")
                    if len(new_titles) > 5:
                        console.print(f"  [dim]... 还有 {len(new_titles) - 5} 集[/dim]")
            except Exception as e:
                console.print(f"[red]✗ 刷新失败: {e}[/red]")
    else:
        console.print("[yellow]请指定播客 ID 或使用 --all 刷新全部[/yellow]")


@cli.command()
@click.argument("podcast_id", callback=validate_podcast_id)
@click.option("--unplayed", "-u", is_flag=True, help="只显示未播放的")
@click.option("--limit", "-n", type=int, default=0, help="显示的数量")
@click.option("--add-queue", "-q", is_flag=True, help="将所有未听节目加入稍后收听")
def episodes(podcast_id, unplayed, limit, add_queue):
    """查看某个播客的节目列表"""
    podcast = database.get_podcast_by_id(podcast_id)
    episodes_list = database.get_episodes_by_podcast(
        podcast_id, only_unplayed=unplayed, limit=limit
    )

    if not episodes_list:
        console.print(f"[yellow]{podcast['title']} 没有符合条件的节目[/yellow]")
        return

    if add_queue:
        unplayed_ids = [ep["id"] for ep in episodes_list if not ep["is_listened"]]
        if not unplayed_ids:
            console.print("[yellow]没有未听节目可添加[/yellow]")
            return
        added = database.batch_add_to_queue(unplayed_ids)
        console.print(f"[green]✓ 已添加 {added} 集到稍后收听队列[/green]")
        return

    title_suffix = "（未播放）" if unplayed else ""
    table = Table(
        title=f"{podcast['title']}{title_suffix}",
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("ID", style="cyan", justify="right", width=5)
    table.add_column("标题", style="bold")
    table.add_column("时长", justify="right", width=10)
    table.add_column("发布日期", style="dim", width=12)
    table.add_column("状态", justify="center", width=8)
    table.add_column("进度", justify="right", width=8)

    for ep in episodes_list:
        duration = format_duration(ep["duration"]) if ep["duration"] else "未知"
        pub_date = ep["pub_date"][:10] if ep["pub_date"] else "未知"

        if ep["is_listened"]:
            status = "[green]✓ 已听[/green]"
        elif ep["progress"] and ep["progress"] > 0:
            status = "[yellow]⏸ 在听[/yellow]"
        else:
            status = "[dim]未听[/dim]"

        if ep["duration"] and ep["duration"] > 0 and ep["progress"]:
            progress_pct = int((ep["progress"] / ep["duration"]) * 100)
            progress_str = f"{progress_pct}%"
        else:
            progress_str = "-"

        table.add_row(
            str(ep["id"]),
            ep["title"],
            duration,
            pub_date,
            status,
            progress_str,
        )

    console.print(table)


@cli.command(name="unplayed")
@click.option("--add-queue", "-q", is_flag=True, help="将所有未听节目加入稍后收听")
def unplayed_episodes(add_queue):
    """按订阅源分组查看所有未播放的节目"""
    podcasts = database.get_all_podcasts()
    has_unplayed = False
    all_unplayed_ids = []

    for podcast in podcasts:
        episodes_list = database.get_episodes_by_podcast(
            podcast["id"], only_unplayed=True
        )
        if not episodes_list:
            continue

        has_unplayed = True

        if add_queue:
            all_unplayed_ids.extend([ep["id"] for ep in episodes_list])
            continue

        console.print()
        console.print(f"[bold green]▸ {podcast['title']}[/bold green] [dim]({len(episodes_list)} 集未听)[/dim]")

        for i, ep in enumerate(episodes_list[:5], 1):
            duration = format_duration(ep["duration"]) if ep["duration"] else "?"
            pub_date = ep["pub_date"][:10] if ep["pub_date"] else "?"

            progress_info = ""
            if ep["progress"] and ep["progress"] > 0:
                progress_pct = int((ep["progress"] / ep["duration"]) * 100) if ep["duration"] else 0
                progress_info = f" [yellow]({progress_pct}%)[/yellow]"

            console.print(f"  [{ep['id']}] {ep['title']} [dim]({duration} · {pub_date})[/dim]{progress_info}")

        if len(episodes_list) > 5:
            console.print(f"  [dim]... 还有 {len(episodes_list) - 5} 集[/dim]")

    if add_queue and all_unplayed_ids:
        added = database.batch_add_to_queue(all_unplayed_ids)
        console.print(f"[green]✓ 已添加 {added} 集未听节目到稍后收听队列[/green]")
        return

    if not has_unplayed:
        console.print("[green]🎉 太棒了！所有节目都听完了[/green]")


@cli.command()
@click.argument("episode_id", callback=validate_episode_id)
@click.option("--continue", "-c", "auto_continue", is_flag=True, help="播完后自动播放队列下一集")
def play(episode_id, auto_continue):
    """播放某集节目"""
    player.play_episode(episode_id, auto_continue=auto_continue)


@cli.command(name="mark-listened")
@click.argument("episode_id", callback=validate_episode_id)
@click.option("--unmark", is_flag=True, help="取消标记已听")
def mark_listened(episode_id, unmark):
    """标记某期节目为已听/未听"""
    database.mark_episode_listened(episode_id, listened=not unmark)
    episode = database.get_episode_by_id(episode_id)
    status = "已听" if not unmark else "未听"
    console.print(f"[green]✓ 已将「{episode['title']}」标记为{status}[/green]")


@cli.command(name="set-progress")
@click.argument("episode_query")
@click.argument("progress")
def set_progress(episode_query, progress):
    """手动设置收听进度 (如: set-progress AI趋势 35% 或 set-progress 12 12:30)"""
    eid = resolve_episode_id(episode_query)
    if eid < 0:
        return

    episode = database.get_episode_by_id(eid)
    total_duration = episode["duration"] or 0

    progress_seconds = parse_progress_input(progress, total_duration)

    if progress_seconds <= 0 and not (progress in ("0", "0%", "0:00")):
        console.print("[red]✗ 无法解析进度值，请使用百分比(35%)、时间(12:30)或秒数[/red]")
        return

    if progress.endswith("%") and total_duration == 0:
        console.print("[yellow]⚠ 该集总时长未知，无法使用百分比进度[/yellow]")
        console.print(f"请使用具体时间，如: podcast set-progress {eid} 12:30")
        return

    new_progress, was_completed, was_reverted = database.set_episode_progress(eid, progress_seconds)

    console.print(f"[green]✓ 已设置进度[/green]")
    console.print(f"  [{eid}] {episode['title']}")
    if total_duration > 0:
        pct = (new_progress / total_duration) * 100
        console.print(f"  当前: {format_duration(new_progress)} / {format_duration(total_duration)} ({pct:.0f}%)")
    else:
        console.print(f"  当前: {format_duration(new_progress)} (总时长未知)")

    if was_completed:
        console.print(f"  [green]进度超过 90%，已自动标记为已听[/green]")
    if was_reverted:
        console.print(f"  [yellow]进度降低，已从已听改回正在听[/yellow]")


@cli.command()
@click.argument("keyword", required=False, default="")
@click.option("--status", "-s", "status_filter",
              type=click.Choice(["all", "unplayed", "in_progress", "listened"]),
              default="all", help="按状态过滤")
@click.option("--podcast", "-p", "podcast_id", callback=validate_podcast_id,
              type=int, help="只搜索某个播客")
@click.option("--limit", "-n", type=int, default=30, help="最多显示多少条")
@click.option("--add-queue", "-q", is_flag=True, help="将搜索结果加入稍后收听")
def search(keyword, status_filter, podcast_id, limit, add_queue):
    """按关键词搜索节目，支持按状态过滤"""
    results = database.search_episodes(
        keyword=keyword,
        status_filter=status_filter,
        podcast_id=podcast_id,
        limit=limit,
    )

    if not results:
        console.print("[yellow]没有找到匹配的节目[/yellow]")
        return

    if add_queue:
        ids = [ep["id"] for ep in results if not ep["is_listened"]]
        if not ids:
            console.print("[yellow]搜索结果中没有未听节目可添加[/yellow]")
            return
        added = database.batch_add_to_queue(ids)
        console.print(f"[green]✓ 已添加 {added} 集到稍后收听队列[/green]")
        return

    status_labels = {
        "all": "全部",
        "unplayed": "未听",
        "in_progress": "在听",
        "listened": "已听",
    }

    title_parts = []
    if keyword:
        title_parts.append(f"「{keyword}」")
    title_parts.append(f"搜索结果 ({status_labels[status_filter]})")
    title = " ".join(title_parts)

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("ID", style="cyan", justify="right", width=5)
    table.add_column("播客", style="dim", width=20)
    table.add_column("标题", style="bold")
    table.add_column("时长", justify="right", width=9)
    table.add_column("状态", justify="center", width=7)
    table.add_column("进度", justify="right", width=7)

    for ep in results:
        duration = format_duration(ep["duration"]) if ep["duration"] else "?"
        podcast_title = ep["podcast_title"] or "未知"
        if len(podcast_title) > 18:
            podcast_title = podcast_title[:16] + "…"

        if ep["is_listened"]:
            status = "[green]✓已听[/green]"
        elif ep["progress"] and ep["progress"] > 0:
            status = "[yellow]⏸在听[/yellow]"
        else:
            status = "[dim]未听[/dim]"

        if ep["duration"] and ep["duration"] > 0 and ep["progress"]:
            progress_pct = int((ep["progress"] / ep["duration"]) * 100)
            progress_str = f"{progress_pct}%"
        else:
            progress_str = "-"

        table.add_row(
            str(ep["id"]),
            podcast_title,
            ep["title"],
            duration,
            status,
            progress_str,
        )

    console.print(table)
    console.print(f"[dim]共找到 {len(results)} 集 | 使用 -q 将结果加入稍后收听[/dim]")


@cli.group()
def queue():
    """稍后收听队列管理"""
    pass


@queue.command(name="list")
def queue_list():
    """查看稍后收听队列"""
    items = database.get_queue()
    if not items:
        console.print("[yellow]稍后收听队列为空[/yellow]")
        return

    table = Table(title="稍后收听队列", box=box.ROUNDED)
    table.add_column("#", justify="right", width=3)
    table.add_column("ID", style="cyan", justify="right", width=5)
    table.add_column("播客", style="dim", width=20)
    table.add_column("标题", style="bold")
    table.add_column("时长", justify="right", width=9)
    table.add_column("状态", justify="center", width=7)

    for i, item in enumerate(items, 1):
        duration = format_duration(item["duration"]) if item["duration"] else "?"
        podcast_title = item["podcast_title"] or "未知"
        if len(podcast_title) > 18:
            podcast_title = podcast_title[:16] + "…"

        if item["is_listened"]:
            status = "[green]✓已听[/green]"
        elif item["progress"] and item["progress"] > 0:
            status = "[yellow]⏸在听[/yellow]"
        else:
            status = "[dim]未听[/dim]"

        table.add_row(
            str(i),
            str(item["episode_id"]),
            podcast_title,
            item["episode_title"],
            duration,
            status,
        )

    console.print(table)
    console.print(f"[dim]共 {len(items)} 集 | 使用 queue next --continue 连续播放[/dim]")


@queue.command(name="add")
@click.argument("episode_ids", nargs=-1, required=True)
def queue_add(episode_ids):
    """添加节目到稍后收听队列 (支持多个ID)"""
    added = 0
    for eid_str in episode_ids:
        try:
            eid = int(eid_str)
            ep = database.get_episode_by_id(eid)
            if not ep:
                console.print(f"[red]✗ ID {eid} 不存在[/red]")
                continue
            if database.is_in_queue(eid):
                console.print(f"[yellow]「{ep['title']}」已在队列中[/yellow]")
                continue
            if database.add_to_queue(eid):
                console.print(f"[green]✓ + {ep['title']}[/green]")
                added += 1
        except ValueError:
            matches = database.find_episode_by_query(eid_str)
            if len(matches) == 1:
                ep = matches[0]
                if not database.is_in_queue(ep["id"]):
                    database.add_to_queue(ep["id"])
                    console.print(f"[green]✓ + {ep['title']}[/green]")
                    added += 1
                else:
                    console.print(f"[yellow]「{ep['title']}」已在队列中[/yellow]")
            elif len(matches) > 1:
                console.print(f"[yellow]「{eid_str}」匹配多个节目，请使用ID[/yellow]")
            else:
                console.print(f"[red]✗ 找不到「{eid_str}」[/red]")

    console.print(f"[dim]共添加 {added} 集到队列[/dim]")


@queue.command(name="remove")
@click.argument("episode_ids", nargs=-1, required=True)
def queue_remove(episode_ids):
    """从稍后收听队列移除节目"""
    removed = 0
    for eid_str in episode_ids:
        try:
            eid = int(eid_str)
            episode = database.get_episode_by_id(eid)
            if episode and database.remove_from_queue(eid):
                console.print(f"[green]✓ 已移除: {episode['title']}[/green]")
                removed += 1
            else:
                console.print(f"[yellow]ID {eid} 不在队列中[/yellow]")
        except ValueError:
            console.print(f"[red]无效ID: {eid_str}[/red]")
    console.print(f"[dim]共移除 {removed} 集[/dim]")


@queue.command(name="top")
@click.argument("episode_id", callback=validate_episode_id)
def queue_top(episode_id):
    """将节目置顶到队列最前面"""
    if database.move_queue_to_top(episode_id):
        episode = database.get_episode_by_id(episode_id)
        console.print(f"[green]✓ 已置顶: {episode['title']}[/green]")
    else:
        console.print("[red]✗ 置顶失败，该节目可能不在队列中[/red]")


@queue.command(name="move")
@click.argument("episode_id", callback=validate_episode_id)
@click.argument("direction", type=click.Choice(["up", "down"]))
def queue_move(episode_id, direction):
    """调整队列中节目顺序 (up/down)"""
    if database.move_queue_item(episode_id, direction):
        episode = database.get_episode_by_id(episode_id)
        label = "上移" if direction == "up" else "下移"
        console.print(f"[green]✓ 已{label}: {episode['title']}[/green]")
    else:
        console.print("[yellow]无法移动，已在边界或不在队列中[/yellow]")


@queue.command(name="skip")
def queue_skip():
    """跳过队列当前第一集"""
    next_item = database.skip_queue_current()
    if next_item is None:
        console.print("[yellow]队列为空[/yellow]")
        return

    console.print(f"[green]✓ 已跳过当前集[/green]")
    console.print(f"[dim]下一集: {next_item['episode_title']}[/dim]")


@queue.command(name="next")
@click.option("--continue", "-c", "auto_continue", is_flag=True, help="播完后自动播放下一集")
def queue_next(auto_continue):
    """播放队列中下一集"""
    items = database.get_queue()
    if not items:
        console.print("[yellow]队列为空[/yellow]")
        return

    first = items[0]
    console.print(f"[green]▶ 播放队列下一集:[/green] {first['episode_title']}")
    console.print(f"[dim]来自: {first['podcast_title']}[/dim]")
    if len(items) > 1:
        console.print(f"[dim]队列中还有 {len(items) - 1} 集[/dim]")
    console.print()

    database.remove_from_queue(first["episode_id"])
    player.play_episode(first["episode_id"], auto_continue=auto_continue)


@queue.command(name="clear")
def queue_clear():
    """清空稍后收听队列"""
    if click.confirm("确定要清空稍后收听队列吗？"):
        items = database.get_queue()
        for item in items:
            database.remove_from_queue(item["episode_id"])
        console.print(f"[green]✓ 已清空队列，移除了 {len(items)} 集[/green]")


@cli.command(name="import-opml")
@click.argument("file_path")
@click.option("--fetch", "-f", is_flag=True, help="导入后立即抓取每个源的最新节目")
def import_opml_cmd(file_path, fetch):
    """从OPML文件导入播客订阅"""
    try:
        imported, skipped = opml.import_opml(file_path)
        console.print(f"[green]✓ 导入完成[/green]")
        console.print(f"  新增: {imported} 个播客")
        console.print(f"  跳过(已存在): {skipped} 个播客")

        if fetch and imported > 0:
            console.print()
            console.print("[bold cyan]正在抓取新导入的播客源...[/bold cyan]")

            newly_added = []
            podcasts = database.get_all_podcasts()
            for p in podcasts:
                if not p["last_updated"]:
                    newly_added.append(p)

            success_count = 0
            fail_count = 0
            total_new = 0
            results = []

            with console.status("[bold green]正在抓取节目..."):
                for podcast in newly_added:
                    try:
                        new_count, new_titles = rss_fetcher.refresh_podcast(podcast["id"])
                        total_new += new_count
                        success_count += 1
                        results.append((podcast["title"], True, new_count, ""))
                    except Exception as e:
                        fail_count += 1
                        results.append((podcast["title"], False, 0, str(e)))

            console.print()
            if success_count > 0:
                console.print(f"[green]✓ 成功抓取 {success_count} 个源，共 {total_new} 集节目[/green]")
            if fail_count > 0:
                console.print(f"[red]✗ 失败 {fail_count} 个源[/red]")

            console.print()
            table = Table(title="抓取详情", box=box.ROUNDED)
            table.add_column("状态", justify="center", width=6)
            table.add_column("播客名称", style="bold")
            table.add_column("节目数", justify="right", width=6)
            table.add_column("失败原因", style="red")

            for title, ok, count, error in results:
                status_icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                count_str = str(count) if ok else "-"
                table.add_row(status_icon, title, count_str, error if not ok else "")

            console.print(table)

    except Exception as e:
        console.print(f"[red]✗ 导入失败: {e}[/red]")


@cli.command()
@click.argument("file_path")
def export_opml(file_path):
    """导出播客订阅为OPML文件"""
    try:
        count = opml.export_opml(file_path)
        console.print(f"[green]✓ 已导出 {count} 个播客到 {file_path}[/green]")
    except Exception as e:
        console.print(f"[red]✗ 导出失败: {e}[/red]")


@cli.command()
@click.argument("file_path")
def export_txt(file_path):
    """导出未播放节目列表为纯文本"""
    try:
        count = player.export_unplayed_txt(file_path)
        console.print(f"[green]✓ 已导出 {count} 集未播放节目到 {file_path}[/green]")
    except Exception as e:
        console.print(f"[red]✗ 导出失败: {e}[/red]")


@cli.command()
def statistics():
    """查看收听统计"""
    overall = stats.get_overall_stats()
    daily_history = stats.get_daily_listen_history(7)
    ranked = stats.get_podcasts_ranked_by_skip_rate()

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]📊 收听统计[/bold cyan]\n\n"
        f"订阅播客: [bold]{overall['total_podcasts']}[/bold] 个\n"
        f"节目总数: [bold]{overall['total_episodes']}[/bold] 集\n"
        f"已听节目: [green]{overall['listened_episodes']}[/green] 集\n"
        f"未听节目: [yellow]{overall['unplayed_episodes']}[/yellow] 集\n\n"
        f"本周收听: [bold green]{format_duration(overall['weekly_listened'])}[/bold green]\n"
        f"累计收听: [bold]{format_duration(overall['total_listened'])}[/bold]\n\n"
        f"跳过率: [red]{overall['overall_skip_rate'] * 100:.1f}%[/red]\n"
        f"播放会话: {overall['total_sessions']} 次",
        title="总览",
        border_style="cyan",
    ))

    console.print()
    console.print("[bold cyan]📅 近7天收听时长[/bold cyan]")
    max_duration = max([d for _, d in daily_history] + [1])
    for day, duration in daily_history:
        bar_len = int((duration / max_duration) * 30)
        bar = "█" * bar_len + "░" * (30 - bar_len)
        console.print(f"  {day} [green]{bar}[/green] {format_duration(duration)}")

    console.print()
    table = Table(title="各播客跳过率排名", box=box.ROUNDED)
    table.add_column("排名", justify="right", width=4)
    table.add_column("播客名称", style="bold")
    table.add_column("跳过率", justify="right", width=10)
    table.add_column("未听", justify="right", width=6)

    for i, (podcast, skip_rate, unplayed) in enumerate(ranked, 1):
        skip_rate_str = f"{skip_rate * 100:.1f}%"
        if skip_rate > 0.5:
            rate_style = "red"
        elif skip_rate > 0.2:
            rate_style = "yellow"
        elif skip_rate > 0:
            rate_style = "green"
        else:
            rate_style = "dim"

        medal = ""
        if i == 1 and skip_rate > 0:
            medal = "🥇 "
        elif i == 2 and skip_rate > 0:
            medal = "🥈 "
        elif i == 3 and skip_rate > 0:
            medal = "🥉 "

        table.add_row(
            str(i),
            f"{medal}{podcast['title']}",
            f"[{rate_style}]{skip_rate_str}[/{rate_style}]",
            str(unplayed),
        )

    console.print(table)


@cli.command()
@click.argument("podcast_id", callback=validate_podcast_id)
def remove(podcast_id):
    """删除一个播客订阅"""
    podcast = database.get_podcast_by_id(podcast_id)
    if click.confirm(f"确定要删除「{podcast['title']}」吗？所有节目数据也会被删除。"):
        database.delete_podcast(podcast_id)
        console.print(f"[green]✓ 已删除 {podcast['title']}[/green]")


@cli.command(name="recent")
@click.option("--days", "-d", type=int, default=7, help="显示最近几天的节目")
@click.option("--all-status", is_flag=True, help="包含已听节目")
def recent_episodes(days, all_status):
    """查看最近新增的节目"""
    only_unplayed = not all_status
    eps = database.get_recent_episodes(days=days, only_unplayed=only_unplayed)

    if not eps:
        status_hint = "未听" if only_unplayed else ""
        console.print(f"[yellow]近 {days} 天没有新增{status_hint}节目[/yellow]")
        return

    label = "未听" if only_unplayed else "全部"
    table = Table(title=f"近 {days} 天新增节目 ({label})", box=box.ROUNDED)
    table.add_column("ID", style="cyan", justify="right", width=5)
    table.add_column("播客", style="dim", width=20)
    table.add_column("标题", style="bold")
    table.add_column("时长", justify="right", width=9)
    table.add_column("发布日期", style="dim", width=12)
    table.add_column("状态", justify="center", width=7)

    for ep in eps[:20]:
        duration = format_duration(ep["duration"]) if ep["duration"] else "?"
        podcast_title = ep.get("podcast_title", "未知")
        if len(podcast_title) > 18:
            podcast_title = podcast_title[:16] + "…"
        pub_date = ep["pub_date"][:10] if ep["pub_date"] else "?"

        if ep["is_listened"]:
            status = "[green]✓已听[/green]"
        elif ep["progress"] and ep["progress"] > 0:
            status = "[yellow]⏸在听[/yellow]"
        else:
            status = "[dim]未听[/dim]"

        table.add_row(
            str(ep["id"]),
            podcast_title,
            ep["title"],
            duration,
            pub_date,
            status,
        )

    console.print(table)
    if len(eps) > 20:
        console.print(f"[dim]显示前 20 集，共 {len(eps)} 集[/dim]")


def main():
    cli()


if __name__ == "__main__":
    main()
