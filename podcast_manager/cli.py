import click
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

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
def refresh(podcast_id, all):
    """刷新播客，获取最新节目"""
    if all:
        with console.status("[bold green]正在刷新所有播客..."):
            success, new_count, results = rss_fetcher.refresh_all_podcasts()
            console.print(f"[green]✓ 刷新了 {success} 个播客，新增 {new_count} 集节目[/green]")

            failed = [r for r in results if not r[2]]
            if failed:
                console.print()
                console.print(f"[yellow]失败 ({len(failed)} 个):[/yellow]")
                for title, url, _ in failed:
                    console.print(f"  [red]✗[/red] {title}")
    elif podcast_id:
        podcast = database.get_podcast_by_id(podcast_id)
        with console.status(f"[bold green]正在刷新 {podcast['title']}..."):
            try:
                new_count = rss_fetcher.refresh_podcast(podcast_id)
                console.print(f"[green]✓ 刷新完成，新增 {new_count} 集节目[/green]")
            except Exception as e:
                console.print(f"[red]✗ 刷新失败: {e}[/red]")
    else:
        console.print("[yellow]请指定播客 ID 或使用 --all 刷新全部[/yellow]")


@cli.command()
@click.argument("podcast_id", callback=validate_podcast_id)
@click.option("--unplayed", "-u", is_flag=True, help="只显示未播放的")
@click.option("--limit", "-n", type=int, default=0, help="显示的数量")
def episodes(podcast_id, unplayed, limit):
    """查看某个播客的节目列表"""
    podcast = database.get_podcast_by_id(podcast_id)
    episodes_list = database.get_episodes_by_podcast(
        podcast_id, only_unplayed=unplayed, limit=limit
    )

    if not episodes_list:
        console.print(f"[yellow]{podcast['title']} 没有符合条件的节目[/yellow]")
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
def unplayed_episodes():
    """按订阅源分组查看所有未播放的节目"""
    podcasts = database.get_all_podcasts()
    has_unplayed = False

    for podcast in podcasts:
        episodes_list = database.get_episodes_by_podcast(
            podcast["id"], only_unplayed=True
        )
        if not episodes_list:
            continue

        has_unplayed = True
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
@click.argument("episode_id", callback=validate_episode_id)
@click.argument("progress")
def set_progress(episode_id, progress):
    """手动设置某集的收听进度 (如 35% 或 12:30 或 450)"""
    episode = database.get_episode_by_id(episode_id)
    total_duration = episode["duration"] or 0

    progress_seconds = parse_progress_input(progress, total_duration)

    if progress_seconds <= 0 and not (progress == "0" or progress == "0%" or progress == "0:00"):
        console.print("[red]✗ 无法解析进度值，请使用百分比(35%)、时间(12:30)或秒数[/red]")
        return

    if progress.endswith("%") and total_duration == 0:
        console.print("[yellow]⚠ 该集总时长未知，无法使用百分比进度[/yellow]")
        console.print("请使用具体时间，如: podcast set-progress {id} 12:30".format(id=episode_id))
        return

    new_progress, was_completed = database.set_episode_progress(episode_id, progress_seconds)

    console.print(f"[green]✓ 已设置进度[/green]")
    if total_duration > 0:
        pct = (new_progress / total_duration) * 100 if total_duration > 0 else 0
        console.print(f"  当前: {format_duration(new_progress)} / {format_duration(total_duration)} ({pct:.0f}%)")
    else:
        console.print(f"  当前: {format_duration(new_progress)} (总时长未知)")

    if was_completed:
        console.print(f"  [green]进度超过 90%，已自动标记为已听[/green]")


@cli.command()
@click.argument("keyword", required=False, default="")
@click.option("--status", "-s", "status_filter",
              type=click.Choice(["all", "unplayed", "in_progress", "listened"]),
              default="all", help="按状态过滤")
@click.option("--podcast", "-p", "podcast_id", callback=validate_podcast_id,
              type=int, help="只搜索某个播客")
@click.option("--limit", "-n", type=int, default=30, help="最多显示多少条")
def search(keyword, status_filter, podcast_id, limit):
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
    console.print(f"[dim]共找到 {len(results)} 集[/dim]")


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
    console.print(f"[dim]共 {len(items)} 集在队列中[/dim]")


@queue.command(name="add")
@click.argument("episode_id", callback=validate_episode_id)
def queue_add(episode_id):
    """添加节目到稍后收听队列"""
    episode = database.get_episode_by_id(episode_id)
    if database.is_in_queue(episode_id):
        console.print(f"[yellow]「{episode['title']}」已在队列中[/yellow]")
        return

    if database.add_to_queue(episode_id):
        console.print(f"[green]✓ 已添加到队列:[/green] {episode['title']}")
    else:
        console.print(f"[red]✗ 添加失败[/red]")


@queue.command(name="remove")
@click.argument("episode_id", callback=validate_episode_id)
def queue_remove(episode_id):
    """从稍后收听队列移除节目"""
    episode = database.get_episode_by_id(episode_id)
    if database.remove_from_queue(episode_id):
        console.print(f"[green]✓ 已从队列移除:[/green] {episode['title']}")
    else:
        console.print(f"[yellow]「{episode['title']}」不在队列中[/yellow]")


@queue.command(name="next")
def queue_next():
    """播放队列中下一集"""
    item = database.pop_queue()
    if not item:
        console.print("[yellow]队列为空[/yellow]")
        return

    console.print(f"[green]▶ 播放队列下一集:[/green] {item['episode_title']}")
    console.print(f"[dim]来自: {item['podcast_title']}[/dim]")
    console.print()
    player.play_episode(item["episode_id"])


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
            console.print()

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
                        new_count = rss_fetcher.refresh_podcast(podcast["id"])
                        total_new += new_count
                        success_count += 1
                        results.append((podcast["title"], podcast["feed_url"], True, new_count))
                    except Exception as e:
                        fail_count += 1
                        results.append((podcast["title"], podcast["feed_url"], False, 0))

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

            for title, url, ok, count in results:
                status_icon = "[green]✓[/green]" if ok else "[red]✗[/red]"
                count_str = str(count) if ok else "-"
                table.add_row(status_icon, title, count_str)

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


def main():
    cli()


if __name__ == "__main__":
    main()
