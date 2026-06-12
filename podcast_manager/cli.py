import os
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

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".podcast_manager", "cache")


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
@click.option("--export", "-e", "export_file", default=None, help="导出本次新增节目清单到文件")
def refresh(podcast_id, all, recent, days, export_file):
    """刷新播客，获取最新节目"""
    if all:
        with console.status("[bold green]正在刷新所有播客..."):
            success, new_count, calibrated_count, results = rss_fetcher.refresh_all_podcasts()

        console.print()
        summary_parts = [f"刷新了 {success} 个播客", f"新增 {new_count} 集节目"]
        if calibrated_count > 0:
            summary_parts.append(f"校准 {calibrated_count} 集时长")
        console.print(f"[green]✓ {'，'.join(summary_parts)}[/green]")

        console.print()
        table = Table(title="刷新详情", box=box.ROUNDED)
        table.add_column("状态", justify="center", width=5)
        table.add_column("播客名称", style="bold")
        table.add_column("新增", justify="right", width=5)
        table.add_column("校准", justify="right", width=5)
        table.add_column("详情", style="dim")

        for r in results:
            if r["success"]:
                icon = "[green]✓[/green]"
                detail = ", ".join(r["new_titles"][:3]) if r["new_titles"] else "无新增"
                if len(r["new_titles"]) > 3:
                    detail += f" 等{len(r['new_titles'])}集"
                cal_str = str(r["calibrated"]) if r["calibrated"] > 0 else "-"
            else:
                icon = "[red]✗[/red]"
                detail = r["error"] or "未知错误"
                cal_str = "-"
            table.add_row(icon, r["title"], str(r["new_count"]) if r["success"] else "-", cal_str, detail)

        console.print(table)

        if export_file and new_count > 0:
            all_new_titles = []
            for r in results:
                if r["success"] and r["new_titles"]:
                    all_new_titles.append((r["title"], r["new_titles"]))
            _export_refresh_list(export_file, all_new_titles)

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
                new_count, new_titles, calibrated = rss_fetcher.refresh_podcast(podcast_id)
                msg = f"[green]✓ 刷新完成，新增 {new_count} 集节目[/green]"
                if calibrated > 0:
                    msg += f"[green]，校准 {calibrated} 集时长[/green]"
                console.print(msg)
                if new_titles:
                    for t in new_titles[:5]:
                        console.print(f"  [dim]+ {t}[/dim]")
                    if len(new_titles) > 5:
                        console.print(f"  [dim]... 还有 {len(new_titles) - 5} 集[/dim]")
            except Exception as e:
                console.print(f"[red]✗ 刷新失败: {e}[/red]")
    else:
        console.print("[yellow]请指定播客 ID 或使用 --all 刷新全部[/yellow]")


def _export_refresh_list(file_path: str, data: list):
    import time as _time
    lines = []
    lines.append("=" * 60)
    lines.append("刷新新增节目清单")
    lines.append(f"导出时间: {_time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")
    total = 0
    for podcast_title, titles in data:
        total += len(titles)
        lines.append(f"【{podcast_title}】({len(titles)} 集)")
        for i, t in enumerate(titles, 1):
            lines.append(f"  {i}. {t}")
        lines.append("")
    lines.append("=" * 60)
    lines.append(f"总计: {total} 集新增")
    lines.append("=" * 60)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    console.print(f"[green]✓ 已导出新增节目清单到 {file_path}[/green]")


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
def play(episode_id):
    """播放某集节目"""
    player.play_episode(episode_id)


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
        console.print(f"  [yellow]进度低于 90%，已从已听改回正在听[/yellow]")


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


QUEUE_STATUS_LABELS = {
    "playing": ("▶ 播放中", "bold cyan"),
    "queued": ("⏳ 排队中", "bold"),
    "skipped": ("⏭ 已跳过", "yellow"),
    "failed": ("✗ 播放失败", "red"),
    "completed": ("✓ 已播完", "green"),
}


@queue.command(name="list")
@click.option("--all", "-a", "show_all", is_flag=True, help="显示全部状态（含已播完/已跳过）")
@click.option("--cleanup", is_flag=True, help="清除已播完和已跳过的记录")
def queue_list(show_all, cleanup):
    """查看稍后收听队列"""
    if cleanup:
        removed = database.cleanup_queue_completed()
        if removed > 0:
            console.print(f"[green]✓ 已清理 {removed} 条已完成/已跳过记录[/green]")
        else:
            console.print("[dim]没有需要清理的记录[/dim]")
        return

    items = database.get_queue()
    if not items:
        console.print("[yellow]稍后收听队列为空[/yellow]")
        return

    by_status = {}
    for item in items:
        s = item.get("status", "queued")
        by_status.setdefault(s, []).append(item)

    status_order = ["playing", "queued", "failed", "skipped"]
    if show_all:
        status_order.append("completed")

    for s in status_order:
        group = by_status.get(s, [])
        if not group:
            continue

        label, style = QUEUE_STATUS_LABELS[s]
        console.print()
        console.print(f"[{style}]{label} ({len(group)})[/{style}]")

        for item in group:
            eid = item["episode_id"]
            title = item["episode_title"]
            pt = item["podcast_title"]
            dur = format_duration(item["duration"]) if item["duration"] else "?"

            extra = ""
            if item["progress"] and item["progress"] > 0 and item["duration"]:
                pct = int(item["progress"] / item["duration"] * 100)
                extra = f" ({pct}%)"

            cached = database.get_cached_episode(eid)
            cache_icon = " 📁" if cached and cached["status"] == "cached" else ""

            console.print(f"  [{eid}] {title} [dim]({pt} · {dur})[/dim]{extra}{cache_icon}")

    playing_count = len(by_status.get("playing", []))
    queued_count = len(by_status.get("queued", []))
    failed_count = len(by_status.get("failed", []))

    console.print()
    parts = []
    if playing_count:
        parts.append(f"播放中 {playing_count}")
    parts.append(f"排队 {queued_count}")
    if failed_count:
        parts.append(f"失败 {failed_count}")
    console.print(f"[dim]{' | '.join(parts)} | queue next --continue 连续播放[/dim]")
    if failed_count:
        console.print("[dim]queue retry <ID> 重试失败节目 | queue retry <ID> --bottom 放回队尾[/dim]")


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
    """跳过队列当前播放中的节目"""
    playing = database.get_queue(status_filter="playing")
    if not playing:
        queued = database.get_queue(status_filter="queued")
        if not queued:
            console.print("[yellow]队列为空[/yellow]")
            return
        database.update_queue_status(queued[0]["episode_id"], "skipped")
        console.print(f"[yellow]⏭ 已跳过: {queued[0]['episode_title']}[/yellow]")
    else:
        database.update_queue_status(playing[0]["episode_id"], "skipped")
        console.print(f"[yellow]⏭ 已跳过: {playing[0]['episode_title']}[/yellow]")

    remaining = database.get_queue(status_filter="queued")
    if remaining:
        console.print(f"[dim]下一集: {remaining[0]['episode_title']} (还剩 {len(remaining)} 集排队)[/dim]")
    else:
        console.print("[dim]没有排队的节目了[/dim]")


@queue.command(name="retry")
@click.argument("episode_id", callback=validate_episode_id)
@click.option("--bottom", "-b", is_flag=True, help="放回队尾而不是队首")
def queue_retry(episode_id, bottom):
    """重试失败的节目（默认放回队首，--bottom 放回队尾）"""
    queue_items = database.get_queue()
    in_queue = any(item["episode_id"] == episode_id for item in queue_items)

    if in_queue:
        if database.retry_queue_item(episode_id, to_bottom=bottom):
            episode = database.get_episode_by_id(episode_id)
            pos = "队尾" if bottom else "队首"
            console.print(f"[green]✓ 已放回{pos}: {episode['title']}[/green]")
        else:
            console.print("[red]✗ 操作失败[/red]")
    else:
        episode = database.get_episode_by_id(episode_id)
        status = "queued"
        database.add_to_queue(episode_id, status=status)
        if bottom:
            pass
        else:
            database.move_queue_to_top(episode_id)
        console.print(f"[green]✓ 已加入队列: {episode['title']}[/green]")


@queue.command(name="next")
@click.option("--continue", "-c", "auto_continue", is_flag=True, help="连续播放直到队列空")
def queue_next(auto_continue):
    """播放队列中下一集（--continue 连续播放到队列空）"""
    _play_queue_loop(auto_continue)


def _play_queue_loop(auto_continue: bool):
    while True:
        queued = database.get_queue(status_filter="queued")
        if not queued:
            console.print("[green]队列已空，播放结束[/green]")
            break

        first = queued[0]
        remaining = len(queued) - 1

        database.update_queue_status(first["episode_id"], "playing")
        console.print()
        console.print(f"[bold cyan]▶ 播放: {first['episode_title']}[/bold cyan]")
        console.print(f"[dim]来自: {first['podcast_title']}[/dim]")
        if remaining > 0:
            console.print(f"[dim]队列中还有 {remaining} 集[/dim]")
        console.print()

        duration_listened, was_skipped, was_completed = player.play_episode(first["episode_id"])

        if was_completed or (was_skipped is False and duration_listened > 0):
            database.update_queue_status(first["episode_id"], "completed")
        elif was_skipped:
            database.update_queue_status(first["episode_id"], "skipped")
        else:
            database.update_queue_status(first["episode_id"], "failed")

        if not auto_continue:
            break

        queued_after = database.get_queue(status_filter="queued")
        if not queued_after:
            console.print("[green]队列已空，播放结束[/green]")
            break

        try:
            answer = Prompt.ask(
                f"[bold]继续播放下一集?[/bold] [dim]({queued_after[0]['episode_title']})[/dim]",
                choices=["y", "n", "s"],
                default="y",
                console=console,
            )
            if answer == "n":
                console.print("[yellow]已暂停连续播放[/yellow]")
                break
            elif answer == "s":
                database.update_queue_status(queued_after[0]["episode_id"], "skipped")
                console.print(f"[yellow]⏭ 跳过: {queued_after[0]['episode_title']}[/yellow]")
                continue
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print("[yellow]已暂停连续播放[/yellow]")
            break


@queue.command(name="clear")
@click.option("--keep-playing", is_flag=True, help="保留正在播放的节目")
def queue_clear(keep_playing):
    """清空稍后收听队列"""
    if click.confirm("确定要清空稍后收听队列吗？"):
        items = database.get_queue()
        removed = 0
        for item in items:
            if keep_playing and item.get("status") == "playing":
                continue
            database.remove_from_queue(item["episode_id"])
            removed += 1
        console.print(f"[green]✓ 已清空队列，移除了 {removed} 集[/green]")


@cli.group()
def cache():
    """下载/离线缓存管理"""
    pass


@cache.command(name="download")
@click.argument("episode_ids", nargs=-1, required=True)
@click.option("--queue", "-q", "from_queue", is_flag=True, help="下载队列中所有排队节目")
def cache_download(episode_ids, from_queue):
    """缓存节目到本地 (支持多个ID或 --queue 批量下载)"""
    if from_queue:
        queued = database.get_queue(status_filter="queued")
        playing = database.get_queue(status_filter="playing")
        target_items = playing + queued
        if not target_items:
            console.print("[yellow]队列中没有待下载的节目[/yellow]")
            return
    else:
        target_items = []
        for eid_str in episode_ids:
            try:
                eid = int(eid_str)
                ep = database.get_episode_by_id(eid)
                if ep:
                    target_items.append({"episode_id": eid, "episode_title": ep["title"],
                                         "podcast_title": "", "audio_url": ep["audio_url"],
                                         "duration": ep["duration"]})
            except ValueError:
                console.print(f"[red]无效ID: {eid_str}[/red]")

    if not target_items:
        console.print("[yellow]没有可下载的节目[/yellow]")
        return

    os.makedirs(CACHE_DIR, exist_ok=True)

    success_count = 0
    fail_count = 0
    skip_count = 0

    for item in target_items:
        eid = item["episode_id"]
        title = item.get("episode_title", "未知")
        audio_url = item.get("audio_url", "")

        existing = database.get_cached_episode(eid)
        if existing and existing["status"] == "cached" and os.path.exists(existing["local_path"]):
            console.print(f"[dim]跳过(已缓存): {title}[/dim]")
            skip_count += 1
            continue

        if not audio_url:
            database.add_cached_episode(eid, "", 0, "failed", "无音频链接")
            console.print(f"[red]✗ 无音频链接: {title}[/red]")
            fail_count += 1
            continue

        database.add_cached_episode(eid, "", 0, "downloading", None)
        console.print(f"[cyan]↓ 下载中: {title}[/cyan]")

        local_filename = f"{eid}_{audio_url.split('/')[-1].split('?')[0]}"
        local_path = os.path.join(CACHE_DIR, local_filename)

        try:
            import urllib.request
            urllib.request.urlretrieve(audio_url, local_path)
            file_size = os.path.getsize(local_path)
            database.add_cached_episode(eid, local_path, file_size, "cached", None)
            size_mb = file_size / (1024 * 1024)
            console.print(f"[green]✓ 已缓存: {title} ({size_mb:.1f}MB)[/green]")
            success_count += 1
        except Exception as e:
            database.add_cached_episode(eid, local_path if os.path.exists(local_path) else "",
                                        0, "failed", str(e))
            console.print(f"[red]✗ 下载失败: {title} - {e}[/red]")
            fail_count += 1
            if os.path.exists(local_path):
                os.remove(local_path)

    console.print()
    parts = []
    if success_count:
        parts.append(f"成功 {success_count}")
    if skip_count:
        parts.append(f"跳过 {skip_count}")
    if fail_count:
        parts.append(f"失败 {fail_count}")
    console.print(f"[dim]缓存完成: {' | '.join(parts)}[/dim]")


@cache.command(name="status")
def cache_status():
    """查看缓存状态"""
    cached = database.get_all_cached()
    if not cached:
        console.print("[yellow]没有缓存记录[/yellow]")
        return

    table = Table(title="离线缓存", box=box.ROUNDED)
    table.add_column("ID", style="cyan", justify="right", width=5)
    table.add_column("标题", style="bold")
    table.add_column("播客", style="dim", width=16)
    table.add_column("状态", justify="center", width=10)
    table.add_column("大小", justify="right", width=8)

    for item in cached:
        eid = item["episode_id"]
        title = item.get("episode_title", "?")
        pt = item.get("podcast_title", "?")
        if len(pt) > 14:
            pt = pt[:12] + "…"

        s = item["status"]
        if s == "cached":
            status = "[green]✓ 已缓存[/green]"
        elif s == "downloading":
            status = "[cyan]↓ 下载中[/cyan]"
        elif s == "failed":
            status = f"[red]✗ 失败[/red]"
        else:
            status = f"[dim]{s}[/dim]"

        file_size = item.get("file_size", 0) or 0
        if file_size > 0:
            size_str = f"{file_size / (1024 * 1024):.1f}MB"
        else:
            size_str = "-"

        table.add_row(str(eid), title, pt, status, size_str)

    console.print(table)

    total_size = sum(item.get("file_size", 0) or 0 for item in cached if item["status"] == "cached")
    cached_count = sum(1 for item in cached if item["status"] == "cached")
    failed_count = sum(1 for item in cached if item["status"] == "failed")
    console.print(f"[dim]已缓存 {cached_count} 集 · {total_size / (1024 * 1024):.1f}MB"
                  + (f" · 失败 {failed_count}" if failed_count else "") + "[/dim]")


@cache.command(name="clean")
@click.argument("episode_ids", nargs=-1)
@click.option("--failed", "-f", "clean_failed", is_flag=True, help="只清理失败的缓存记录")
@click.option("--all", "-a", "clean_all", is_flag=True, help="清理全部缓存")
def cache_clean(episode_ids, clean_failed, clean_all):
    """清理缓存文件"""
    if clean_all:
        cached = database.get_all_cached()
        for item in cached:
            local_path = database.delete_cached_episode(item["episode_id"])
            if local_path and os.path.exists(local_path):
                os.remove(local_path)
        console.print(f"[green]✓ 已清理全部 {len(cached)} 个缓存[/green]")
        return

    if clean_failed:
        cached = database.get_all_cached()
        count = 0
        for item in cached:
            if item["status"] == "failed":
                local_path = database.delete_cached_episode(item["episode_id"])
                if local_path and os.path.exists(local_path):
                    os.remove(local_path)
                count += 1
        console.print(f"[green]✓ 已清理 {count} 个失败记录[/green]")
        return

    if not episode_ids:
        console.print("[yellow]请指定节目ID或使用 --failed / --all[/yellow]")
        return

    for eid_str in episode_ids:
        try:
            eid = int(eid_str)
            local_path = database.delete_cached_episode(eid)
            if local_path and os.path.exists(local_path):
                os.remove(local_path)
            console.print(f"[green]✓ 已清理: ID {eid}[/green]")
        except ValueError:
            console.print(f"[red]无效ID: {eid_str}[/red]")


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
                        new_count, new_titles, calibrated = rss_fetcher.refresh_podcast(podcast["id"])
                        total_new += new_count
                        success_count += 1
                        results.append((podcast["title"], True, new_count, "", calibrated))
                    except Exception as e:
                        fail_count += 1
                        results.append((podcast["title"], False, 0, str(e), 0))

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
            table.add_column("校准", justify="right", width=5)
            table.add_column("失败原因", style="red")

            for title, ok, count, error, cal in results:
                status_icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                count_str = str(count) if ok else "-"
                cal_str = str(cal) if cal > 0 else "-"
                table.add_row(status_icon, title, count_str, cal_str, error if not ok else "")

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
@click.option("--today", "-t", is_flag=True, help="只显示今天的节目")
@click.option("--week", "-w", is_flag=True, help="按本周（自然周）显示")
@click.option("--days", "-d", type=int, default=7, help="显示最近几天的节目")
@click.option("--podcast", "-p", "podcast_id", callback=validate_podcast_id,
              type=int, help="按播客筛选")
@click.option("--all-status", is_flag=True, help="包含已听节目")
def recent_episodes(today, week, days, podcast_id, all_status):
    """查看最近新增的节目"""
    only_unplayed = not all_status

    if week:
        eps = database.get_recent_episodes_week(
            podcast_id=podcast_id,
            only_unplayed=only_unplayed,
        )
    else:
        eps = database.get_recent_episodes(
            days=days,
            only_unplayed=only_unplayed,
            podcast_id=podcast_id,
            today_only=today,
        )

    if not eps:
        if today:
            console.print("[yellow]今天没有新增节目[/yellow]")
        elif week:
            label = "本周"
            if podcast_id:
                podcast = database.get_podcast_by_id(podcast_id)
                label += f" {podcast['title']}"
            console.print(f"[yellow]{label}没有新增节目[/yellow]")
        elif podcast_id:
            podcast = database.get_podcast_by_id(podcast_id)
            console.print(f"[yellow]近 {days} 天 {podcast['title']} 没有新增节目[/yellow]")
        else:
            console.print(f"[yellow]近 {days} 天没有新增节目[/yellow]")
        return

    if week:
        time_label = "本周"
    elif today:
        time_label = "今天"
    else:
        time_label = f"近 {days} 天"
    status_label = "未听" if only_unplayed else "全部"

    podcast_counts = {}
    for ep in eps:
        pt = ep.get("podcast_title", "未知")
        podcast_counts[pt] = podcast_counts.get(pt, 0) + 1

    podcast_label = ""
    if podcast_id:
        podcast = database.get_podcast_by_id(podcast_id)
        podcast_label = f" - {podcast['title']}"

    current_podcast = None
    for ep in eps:
        ep_podcast = ep.get("podcast_title", "未知")
        if ep_podcast != current_podcast:
            current_podcast = ep_podcast
            count = podcast_counts.get(current_podcast, 0)
            console.print()
            console.print(f"[bold green]▸ {current_podcast}[/bold green] [dim]({count} 集)[/dim]")

        duration = format_duration(ep["duration"]) if ep["duration"] else "?"
        pub_date = ep["pub_date"][:10] if ep["pub_date"] else "?"

        if ep["is_listened"]:
            status = "[green]✓[/green]"
        elif ep["progress"] and ep["progress"] > 0:
            status = "[yellow]⏸[/yellow]"
        else:
            status = " "

        progress_info = ""
        if ep["progress"] and ep["progress"] > 0 and ep["duration"]:
            pct = int((ep["progress"] / ep["duration"]) * 100)
            progress_info = f" [yellow]({pct}%)[/yellow]"

        console.print(f"  {status} [{ep['id']}] {ep['title']} [dim]({duration} · {pub_date})[/dim]{progress_info}")

    console.print()
    console.print(f"[dim]{time_label}{podcast_label} · {status_label} · 共 {len(eps)} 集[/dim]")


@cli.command(name="history")
@click.argument("episode_query", required=False, default=None)
@click.option("--days", "-d", type=int, default=0, help="显示最近几天的历史")
@click.option("--limit", "-n", type=int, default=20, help="最多显示多少条")
def play_history(episode_query, days, limit):
    """查看播放历史（每次会话的开始/结束位置）"""
    if episode_query:
        eid = resolve_episode_id(episode_query)
        if eid < 0:
            return
        episode = database.get_episode_by_id(eid)
        sessions = database.get_episode_play_history(eid, limit=limit)

        if not sessions:
            console.print(f"[yellow]「{episode['title']}」没有播放记录[/yellow]")
            return

        podcast = database.get_podcast_by_id(episode["podcast_id"])
        podcast_title = podcast["title"] if podcast else "未知"

        console.print()
        console.print(f"[bold cyan]播放历史: {episode['title']}[/bold cyan]")
        console.print(f"[dim]来自: {podcast_title}[/dim]")
        if episode["duration"]:
            console.print(f"[dim]总时长: {format_duration(episode['duration'])}[/dim]")
        console.print()

        table = Table(box=box.ROUNDED)
        table.add_column("#", justify="right", width=3, style="dim")
        table.add_column("开始时间", width=16)
        table.add_column("从", justify="right", width=10)
        table.add_column("到", justify="right", width=10)
        table.add_column("听了", justify="right", width=8)
        table.add_column("结果", justify="center", width=8)

        total_duration = episode["duration"] or 0

        for i, s in enumerate(sessions, 1):
            start_time = s["start_time"][:16] if s["start_time"] else "?"
            start_pos = format_duration(s["start_position"] or 0)
            end_pos = format_duration(s["end_position"] or 0) if s["end_time"] else "-"
            listened = format_duration(s["duration_listened"] or 0)

            if s["was_completed"]:
                result = "[green]✓ 听完[/green]"
            elif s["was_skipped"]:
                result = "[yellow]⏭ 跳过[/yellow]"
            elif s["end_time"]:
                result = "[cyan]⏸ 中断[/cyan]"
            else:
                result = "[dim]进行中[/dim]"

            table.add_row(str(i), start_time, start_pos, end_pos, listened, result)

        console.print(table)

        total_listened = sum(s["duration_listened"] or 0 for s in sessions)
        skipped_count = sum(1 for s in sessions if s["was_skipped"])
        completed_count = sum(1 for s in sessions if s["was_completed"])

        console.print()
        parts = [f"共 {len(sessions)} 次"]
        parts.append(f"累计 {format_duration(total_listened)}")
        if completed_count:
            parts.append(f"听完 {completed_count} 次")
        if skipped_count:
            parts.append(f"跳过 {skipped_count} 次")
        console.print(f"[dim]{' | '.join(parts)}[/dim]")

    else:
        sessions = database.get_play_history(limit=limit, days=days)

        if not sessions:
            console.print("[yellow]没有播放记录[/yellow]")
            return

        console.print()
        console.print(f"[bold cyan]播放历史[/bold cyan]")
        if days:
            console.print(f"[dim]近 {days} 天[/dim]")
        console.print()

        table = Table(box=box.ROUNDED)
        table.add_column("时间", width=16)
        table.add_column("标题", style="bold")
        table.add_column("播客", style="dim", width=14)
        table.add_column("从→到", width=14)
        table.add_column("听了", justify="right", width=8)
        table.add_column("结果", justify="center", width=8)

        for s in sessions:
            start_time = s["start_time"][:16] if s["start_time"] else "?"
            title = s.get("episode_title", "?")
            if len(title) > 24:
                title = title[:22] + "…"
            pt = s.get("podcast_title", "?")
            if len(pt) > 12:
                pt = pt[:10] + "…"

            start_pos = format_duration(s["start_position"] or 0)
            end_pos = format_duration(s["end_position"] or 0)
            range_str = f"{start_pos}→{end_pos}"
            listened = format_duration(s["duration_listened"] or 0)

            if s["was_completed"]:
                result = "[green]✓[/green]"
            elif s["was_skipped"]:
                result = "[yellow]⏭[/yellow]"
            elif s["end_time"]:
                result = "[cyan]⏸[/cyan]"
            else:
                result = "[dim]…[/dim]"

            table.add_row(start_time, title, pt, range_str, listened, result)

        console.print(table)
        console.print(f"[dim]共 {len(sessions)} 条 | history <节目ID> 查看某集详情[/dim]")


def main():
    cli()


if __name__ == "__main__":
    main()
