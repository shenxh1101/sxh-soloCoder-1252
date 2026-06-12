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
            success, new_count = rss_fetcher.refresh_all_podcasts()
            console.print(f"[green]✓ 刷新了 {success} 个播客，新增 {new_count} 集节目[/green]")
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


@cli.command()
@click.argument("file_path")
def import_opml(file_path):
    """从OPML文件导入播客订阅"""
    try:
        imported, skipped = opml.import_opml(file_path)
        console.print(f"[green]✓ 导入完成[/green]")
        console.print(f"  新增: {imported} 个播客")
        console.print(f"  跳过(已存在): {skipped} 个播客")
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
