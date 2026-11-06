"""`kg serve` command handler extracted from the legacy CLI module."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from kg.infrastructure.config.loader import load_config

if TYPE_CHECKING:
    from rich.console import Console


def run_serve_command(*, port: int, dev: bool, console: Console, host: str = "127.0.0.1") -> None:
    """Execute the `kg serve` CLI behavior without Click wiring."""
    import subprocess

    import uvicorn

    config = load_config()
    project_root = config.root_path

    if dev:
        web_dir = project_root / "web"
        if not (web_dir / "package.json").exists():
            console.print(f"[red]前端项目未找到: {web_dir}[/]")
            console.print("[dim]请先在 web/ 目录下运行 npm install[/]")
            sys.exit(1)

        console.print("[bold blue]开发模式: 启动 Vite + FastAPI[/]")
        console.print("  Vite dev server: http://localhost:5173")
        console.print(f"  FastAPI backend: http://localhost:{port}")
        console.print()

        vite_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=str(web_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            uvicorn.run("agents.api:app", host=host, port=port, reload=True)
        finally:
            vite_proc.terminate()
            vite_proc.wait()
        return

    static_dir = project_root / "agents" / "static"
    if not static_dir.is_dir():
        console.print("[yellow]提示: 未找到前端构建产物。[/]")
        console.print("[dim]请先运行 cd web && npm run build[/]")
        console.print("[dim]或使用 --dev 进入开发模式。[/]\n")

    console.print("[bold green]Knowledge Graph Web UI[/]")
    console.print(f"  访问: http://localhost:{port}")
    console.print()
    uvicorn.run("agents.api:app", host=host, port=port)
