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


def play_episode(episode_id: int) -> Tuple[int, bool, bool]:
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

    console.print("[yellow]系统播放器已启动...[/yellow]")
    console.print("[dim]终端显示的计时仅供参考，以实际播放器进度为准[/dim]")
    console.print("[dim]按 Ctrl+C 停止播放[/dim]")
    console.print()

    current_position = start_position
    was_skipped = False
    was_completed = False

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
                current_position = elapsed_at_start + elapsed

                if total_duration > 0:
                    if current_position >= total_duration:
                        current_position = total_duration
                        break
                    progress.update(task, completed=current_position)
                else:
                    progress.update(task, completed=current_position if current_position < 100 else 99)

                time.sleep(1)

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]停止播放...[/yellow]")

    duration_listened = max(0, current_position - start_position)

    if total_duration > 0:
        progress_percent = (current_position / total_duration) * 100
        if progress_percent >= COMPLETION_THRESHOLD_PERCENT:
            was_completed = True
            console.print()
            console.print(f"[green]已播放超过 {COMPLETION_THRESHOLD_PERCENT}%，自动标记为已听[/green]")
        elif progress_percent < SKIP_THRESHOLD_PERCENT and duration_listened > 30:
            was_skipped = True
            console.print()
            console.print("[yellow]收听时间较短，标记为跳过[/yellow]")

    database.update_episode_progress(episode_id, current_position)
    database.end_play_session(
        session_id=session_id,
        end_position=current_position,
        duration_listened=duration_listened,
        was_skipped=was_skipped,
        was_completed=was_completed,
    )

    console.print()
    console.print(f"[green]本次收听:[/green] {format_duration(duration_listened)}")
    if total_duration > 0:
        pct = (current_position / total_duration) * 100 if total_duration > 0 else 0
        console.print(f"[green]当前进度:[/green] {format_duration(current_position)} / {format_duration(total_duration)} ({pct:.0f}%)")
    else:
        console.print(f"[green]已播放:[/green] {format_duration(current_position)} (总时长未知)")
    console.print()
    console.print("[dim]提示: 可使用 podcast set-progress 手动调整进度[/dim]")

    return duration_listened, was_skipped, was_completed


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
