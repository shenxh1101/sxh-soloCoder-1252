import os
import sys
import subprocess
import platform
import time
import threading
from typing import Optional, Tuple
from rich.console import Console
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.text import Text
from rich.prompt import Prompt, Confirm

from . import database


SKIP_THRESHOLD_PERCENT = 10
COMPLETION_THRESHOLD_PERCENT = 95


def get_system_player_command() -> str:
    system = platform.system()
    if system == "Windows":
        return "start"
    elif system == "Darwin":
        return "open"
    else:
        return "xdg-open"


def play_audio_system(audio_url: str) -> Optional[subprocess.Popen]:
    system = platform.system()
    try:
        if system == "Windows":
            cmd = ["cmd", "/c", "start", "", audio_url]
            proc = subprocess.Popen(cmd, shell=False)
        elif system == "Darwin":
            cmd = ["open", audio_url]
            proc = subprocess.Popen(cmd)
        else:
            cmd = ["xdg-open", audio_url]
            proc = subprocess.Popen(cmd)
        return proc
    except Exception:
        return None


def format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "0:00"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def parse_time_string(s: str) -> int:
    s = s.strip()
    if not s:
        return 0
    if s.endswith("%"):
        return 0
    try:
        if ":" in s:
            parts = s.split(":")
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
        return int(float(s))
    except (ValueError, TypeError):
        return 0


def play_episode(episode_id: int, auto_continue: bool = False) -> Tuple[int, bool, bool]:
    console = Console()
    episode = database.get_episode_by_id(episode_id)
    if not episode:
        console.print("[red]错误：找不到该集节目[/red]")
        return 0, False, False

    if not episode["audio_url"]:
        console.print("[red]错误：该集没有音频链接[/red]")
        return 0, False, False

    podcast = database.get_podcast_by_id(episode["podcast_id"])
    podcast_title = podcast["title"] if podcast else "未知播客"

    total_duration = episode["duration"] or 0
    start_position = episode["progress"] or 0

    console.print()
    console.print(f"[bold cyan]正在播放:[/bold cyan] {episode['title']}")
    console.print(f"[dim]来自: {podcast_title}[/dim]")
    if total_duration > 0:
        console.print(f"[dim]总时长: {format_duration(total_duration)}[/dim]")
    else:
        console.print(f"[dim]总时长: 未知[/dim]")
    if start_position > 0:
        console.print(f"[dim]从 {format_duration(start_position)} 继续播放[/dim]")
    console.print()

    database.increment_play_count(episode_id)
    session_id = database.start_play_session(episode_id, start_position)

    play_proc = play_audio_system(episode["audio_url"])

    if play_proc is None:
        console.print("[red]✗ 无法启动系统播放器[/red]")
        console.print("[dim]请检查音频链接是否有效[/dim]")
        database.end_play_session(session_id, start_position, 0)
        return 0, False, False

    console.print("[yellow]系统播放器已启动[/yellow]")
    console.print("[dim]终端计时仅供参考，结束时将确认实际进度[/dim]")
    console.print("[dim]按 Ctrl+C 停止并确认进度[/dim]")
    console.print()

    timer_position = start_position

    try:
        if total_duration > 0:
            progress_items = [
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=None),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("•"),
                TimeElapsedColumn(),
                TextColumn("/"),
                TextColumn("{task.fields[total_str]}"),
            ]
            total_for_bar = total_duration
        else:
            progress_items = [
                TextColumn("[bold blue]{task.description}"),
                TextColumn("•"),
                TimeElapsedColumn(),
                TextColumn("[dim](参考计时)[/dim]"),
            ]
            total_for_bar = 1

        with Progress(
            *progress_items,
            console=console,
            transient=False,
        ) as progress:
            task_kwargs = {
                "description": "播放中",
                "total": max(total_for_bar, 1),
                "completed": start_position if total_duration > 0 else 0,
            }
            if total_duration > 0:
                task_kwargs["total_str"] = format_duration(total_duration)
            task = progress.add_task(**task_kwargs)

            start_time = time.time()
            elapsed_at_start = start_position if total_duration > 0 else 0

            while True:
                elapsed = int(time.time() - start_time)
                timer_position = elapsed_at_start + elapsed

                if total_duration > 0:
                    if timer_position >= total_duration:
                        timer_position = total_duration
                        break
                    progress.update(task, completed=timer_position)
                else:
                    progress.update(task, completed=min(timer_position, 99))

                try:
                    play_proc.poll()
                    if play_proc.returncode is not None and play_proc.returncode != 0:
                        console.print()
                        console.print("[yellow]系统播放器似乎已提前关闭[/yellow]")
                        break
                except Exception:
                    pass

                time.sleep(1)

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]停止播放[/yellow]")

    console.print()

    confirmed_pos = _confirm_position(console, timer_position, total_duration)

    duration_listened = max(0, confirmed_pos - start_position)
    was_skipped = False
    was_completed = False

    if total_duration > 0:
        progress_percent = (confirmed_pos / total_duration) * 100
        if progress_percent >= COMPLETION_THRESHOLD_PERCENT:
            was_completed = True
            console.print(f"[green]已播放超过 {COMPLETION_THRESHOLD_PERCENT}%，自动标记为已听[/green]")
        elif progress_percent < SKIP_THRESHOLD_PERCENT and duration_listened > 30:
            was_skipped = True
            console.print("[yellow]收听时间较短，标记为跳过[/yellow]")

    database.update_episode_progress(episode_id, confirmed_pos)
    database.end_play_session(
        session_id=session_id,
        end_position=confirmed_pos,
        duration_listened=duration_listened,
        was_skipped=was_skipped,
        was_completed=was_completed,
    )

    console.print()
    console.print(f"[green]本次收听:[/green] {format_duration(duration_listened)}")
    if total_duration > 0:
        pct = (confirmed_pos / total_duration) * 100
        console.print(f"[green]当前进度:[/green] {format_duration(confirmed_pos)} / {format_duration(total_duration)} ({pct:.0f}%)")
    else:
        console.print(f"[green]已播放:[/green] {format_duration(confirmed_pos)} (总时长未知)")

    return duration_listened, was_skipped, was_completed


def _confirm_position(console: Console, timer_position: int, total_duration: int) -> int:
    suggested_pos = timer_position
    if total_duration > 0:
        suggested_pct = (suggested_pos / total_duration) * 100
        suggested_str = f"{format_duration(suggested_pos)} ({suggested_pct:.0f}%)"
    else:
        suggested_str = format_duration(suggested_pos)

    console.print(f"[cyan]终端参考计时: {suggested_str}[/cyan]")
    console.print()
    confirmed_pos = None

    try:
        answer = Prompt.ask(
            "[bold]请确认实际听到哪里[/bold]",
            default=suggested_str if total_duration > 0 else format_duration(suggested_pos),
            console=console,
        )
        answer = answer.strip()

        if answer.endswith("%"):
            try:
                pct = float(answer[:-1])
                if total_duration > 0:
                    confirmed_pos = int(total_duration * pct / 100)
                else:
                    console.print("[yellow]总时长未知，无法使用百分比，使用参考计时[/yellow]")
                    confirmed_pos = suggested_pos
            except ValueError:
                confirmed_pos = suggested_pos
        elif ":" in answer:
            confirmed_pos = parse_time_string(answer)
        else:
            try:
                confirmed_pos = int(float(answer))
            except (ValueError, TypeError):
                confirmed_pos = suggested_pos

        if confirmed_pos is None or confirmed_pos < 0:
            confirmed_pos = suggested_pos
    except (EOFError, KeyboardInterrupt):
        console.print()
        confirmed_pos = suggested_pos

    if total_duration > 0:
        confirmed_pos = min(confirmed_pos, total_duration)
    confirmed_pos = max(confirmed_pos, 0)

    return confirmed_pos


def export_unplayed_txt(file_path: str) -> int:
    podcasts = database.get_all_podcasts()
    lines = []
    total_unplayed = 0

    lines.append("=" * 60)
    lines.append("未播放节目列表")
    lines.append(f"导出时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")

    for podcast in podcasts:
        episodes = database.get_episodes_by_podcast(podcast["id"], only_unplayed=True)
        if not episodes:
            continue

        total_unplayed += len(episodes)
        lines.append(f"【{podcast['title']}】")
        lines.append("-" * 40)

        for i, ep in enumerate(episodes, 1):
            pub_date = ep["pub_date"][:10] if ep["pub_date"] else "未知"
            duration = format_duration(ep["duration"]) if ep["duration"] else "未知"
            lines.append(f"  {i}. {ep['title']}")
            lines.append(f"     发布日期: {pub_date} | 时长: {duration}")
            if ep["progress"] and ep["progress"] > 0:
                progress_pct = int((ep["progress"] / ep["duration"]) * 100) if ep["duration"] > 0 else 0
                lines.append(f"     收听进度: {format_duration(ep['progress'])} ({progress_pct}%)")
            lines.append("")

        lines.append("")

    lines.append("=" * 60)
    lines.append(f"总计: {total_unplayed} 集未播放")
    lines.append("=" * 60)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return total_unplayed
