"""Registry placeholder for goblins that are intended to run only as containers."""

from goblin_king.contracts import GoblinContext, GoblinResult


def run(input_payload: dict, ctx: GoblinContext) -> GoblinResult:
    """Return a clear failure if a container-only goblin is run in-process."""
    del input_payload, ctx
    return GoblinResult.failed(
        error="This goblin is container-only; run it with --runtime docker."
    )
