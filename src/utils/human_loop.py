"""Human-in-the-Loop helpers — interactive prompts for LLM failure recovery."""

from enum import Enum
from typing import Callable

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

console = Console()


class HumanChoice(str, Enum):
    RETRY = "retry"
    SWITCH_MODEL = "switch_model"
    DATA_ONLY = "data_only"
    ABORT = "abort"


def prompt_llm_failure(task_description: str) -> HumanChoice:
    """
    Called when an LLM API call fails in interactive (CLI) mode.
    Displays a Rich panel and waits for user input.
    """
    console.print(
        Panel(
            f"[bold red]⚠  LLM API 不可用[/bold red]\n\n"
            f"任务: [bold]{task_description}[/bold]\n\n"
            "请选择处理方式:\n"
            "  [bold cyan](1)[/bold cyan] 重试\n"
            "  [bold cyan](2)[/bold cyan] 切换到备用模型\n"
            "  [bold cyan](3)[/bold cyan] 仅输出数据版结果（无 AI 分析）\n"
            "  [bold cyan](4)[/bold cyan] 放弃此任务",
            title="[red]🤖 AI 服务中断",
            border_style="red",
        )
    )
    choice_map = {"1": HumanChoice.RETRY, "2": HumanChoice.SWITCH_MODEL,
                  "3": HumanChoice.DATA_ONLY, "4": HumanChoice.ABORT}
    while True:
        raw = Prompt.ask("请输入选项", choices=["1", "2", "3", "4"])
        return choice_map[raw]


def prompt_investment_confirmation(
    ticker: str,
    suggested_amount: float,
    suggested_pct: float,
) -> tuple[bool, float | None]:
    """
    After a position recommendation, ask the user whether they executed the investment.
    Returns (confirmed: bool, actual_amount: float | None).
    """
    console.print(
        Panel(
            f"[bold]标的:[/bold] {ticker}\n"
            f"[bold]建议投入:[/bold] ¥{suggested_amount:,.0f}"
            f" ([cyan]{suggested_pct:.1%}[/cyan] 总资产)\n\n"
            "是否已执行投资？",
            title="💰 投资执行确认",
            border_style="green",
        )
    )
    confirmed = Prompt.ask("已执行 (y) / 跳过 (n)", choices=["y", "n"]) == "y"
    if not confirmed:
        return False, None

    while True:
        raw = Prompt.ask(f"请输入实际投资金额 (¥), 建议 {suggested_amount:,.0f}")
        try:
            amount = float(raw.replace(",", "").replace("¥", "").strip())
            return True, amount
        except ValueError:
            console.print("[red]请输入有效数字[/red]")
