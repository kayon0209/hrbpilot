"""Command execution owns exactly one application transaction boundary."""

from collections.abc import Awaitable
from typing import Protocol, TypeVar

from app.application.uow import UnitOfWork

TCommand = TypeVar("TCommand", contravariant=True)
TResult = TypeVar("TResult", covariant=True)


class CommandHandler(Protocol[TCommand, TResult]):
    def __call__(self, command: TCommand, uow: UnitOfWork) -> Awaitable[TResult]: ...


class CommandExecutor:
    """Commit on successful command completion, otherwise roll back once."""

    async def execute(
        self,
        command: TCommand,
        handler: CommandHandler[TCommand, TResult],
        uow: UnitOfWork,
    ) -> TResult:
        try:
            result = await handler(command, uow)
            await uow.commit()
            return result
        except Exception:
            await uow.rollback()
            raise
