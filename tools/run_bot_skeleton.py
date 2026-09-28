"""Print bot-skeleton capability status without sending game input."""

from macro_clicker.bot import BotRuntime
from macro_clicker.bot.skeleton_port import SkeletonGamePort


def main() -> None:
    runtime = BotRuntime(SkeletonGamePort())
    for capability in runtime.capabilities():
        print(
            f"{capability.name}: {capability.status.value} - {capability.detail}"
        )


if __name__ == "__main__":
    main()
