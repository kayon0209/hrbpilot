"""Production governed tool executors.

Executors receive only a validated invocation plus the current subject that
the Dispatcher re-authorized.  They never read the legacy TOOL_EXECUTORS
registry.  Every write must use the stable idempotency key supplied by the
Dispatcher.
"""

from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.access.object_scope import resolve_visible_user_ids
from app.data.database import make_tenant_session
from app.data.models.hr_case import HRCase
from app.data.models.notification import InAppNotification
from app.data.models.user import User
from app.outbox.dispatcher import TerminalToolDispatchError, ToolExecutor, ToolInvocation
from app.scenarios.hr_case_agent.service import HRCaseService
from app.scenarios.work_tasks.service import CreateWorkTaskBody, create_work_task
from app.shared.errors import AppError


def _actor(invocation: ToolInvocation) -> str:
    return f"user:{invocation.subject_id}|role:{invocation.subject_role}"


async def _service_for(invocation: ToolInvocation) -> tuple:
    session = await make_tenant_session(invocation.tenant_id)
    visible_user_ids = await resolve_visible_user_ids(
        invocation.tenant_id,
        invocation.subject_id,
        invocation.subject_role,
    )
    return session, HRCaseService(
        session,
        invocation.tenant_id,
        actor=_actor(invocation),
        visible_user_ids=visible_user_ids,
    )


def _terminal_error(error: AppError) -> TerminalToolDispatchError:
    return TerminalToolDispatchError(error.code, error.message)


async def execute_create_work_task(invocation: ToolInvocation) -> dict:
    body = CreateWorkTaskBody(
        **invocation.params,
        idempotency_key=invocation.idempotency_key,
    )
    task = await create_work_task(
        invocation.tenant_id,
        invocation.subject_id,
        invocation.subject_role,
        body,
    )
    return {
        "summary": f"work task {task.task_id} created",
        "task_id": task.task_id,
    }


async def execute_create_hr_case(invocation: ToolInvocation) -> dict:
    """Create one case with a request-derived identity for redelivery safety."""
    created_case_id = str(uuid5(NAMESPACE_URL, f"hrbpilot:case:{invocation.tenant_id}:{invocation.idempotency_key}"))
    session, service = await _service_for(invocation)
    try:
        existing = await session.scalar(
            select(HRCase).where(
                HRCase.id == created_case_id,
                HRCase.tenant_id == invocation.tenant_id,
            )
        )
        if existing is not None:
            return {"summary": f"case {created_case_id} already created", "case_id": created_case_id}
        case = await service.create_case(
            invocation.subject_id,
            invocation.params["subject_ref"],
            invocation.params["category"],
            invocation.params["title"],
            description=invocation.params.get("description"),
            risk_level=invocation.params["risk_level"],
            case_id=created_case_id,
        )
        await session.commit()
        return {"summary": f"case {case.id} created", "case_id": case.id}
    except AppError as error:
        await session.rollback()
        raise _terminal_error(error) from error
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def execute_assign_case_owner(invocation: ToolInvocation) -> dict:
    session, service = await _service_for(invocation)
    try:
        case = await service.assign_case_owner(invocation.case_id, invocation.params["owner_id"])
        await session.commit()
        return {"summary": f"case {case.id} assigned to {case.owner_id}", "owner_id": case.owner_id}
    except AppError as error:
        await session.rollback()
        raise _terminal_error(error) from error
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def execute_update_case_status(invocation: ToolInvocation) -> dict:
    session, service = await _service_for(invocation)
    try:
        case = await service.get_case(invocation.case_id)
        target = invocation.params["status"]
        if case.status != target:
            case = await service.transition_case(case.id, target, reason="governed update_case_status")
        await session.commit()
        return {"summary": f"case {case.id} is {case.status}", "status": case.status}
    except AppError as error:
        await session.rollback()
        raise _terminal_error(error) from error
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def execute_send_case_notification(invocation: ToolInvocation) -> dict:
    """Persist one recipient-visible notification without duplicating case data."""
    session, service = await _service_for(invocation)
    try:
        if invocation.params["channel"] != "in_app":
            raise TerminalToolDispatchError("UNSUPPORTED_NOTIFICATION_CHANNEL", "only in_app delivery is configured")
        await service.get_case(invocation.case_id)
        recipient_user_id = invocation.params["recipient_ref"]
        visible_user_ids = await resolve_visible_user_ids(
            invocation.tenant_id,
            invocation.subject_id,
            invocation.subject_role,
        )
        if recipient_user_id not in visible_user_ids:
            raise TerminalToolDispatchError("RECIPIENT_OUTSIDE_SCOPE", "recipient is outside the current visible scope")
        recipient = await session.scalar(
            select(User.id).where(User.id == recipient_user_id, User.tenant_id == invocation.tenant_id)
        )
        if recipient is None:
            raise TerminalToolDispatchError("RECIPIENT_NOT_FOUND", "recipient user does not exist")
        delivery_key = f"in-app-notification:{invocation.execution_id}"
        notification = await session.scalar(
            select(InAppNotification).where(
                InAppNotification.tenant_id == invocation.tenant_id,
                InAppNotification.delivery_key == delivery_key,
            )
        )
        if notification is None:
            notification = InAppNotification(
                tenant_id=invocation.tenant_id,
                recipient_user_id=recipient_user_id,
                case_id=invocation.case_id,
                tool_execution_id=invocation.execution_id,
                template=invocation.params["template"],
                delivery_key=delivery_key,
            )
            session.add(notification)
            await session.flush()
        await session.commit()
        return {"summary": f"in-app notification {notification.id} delivered", "notification_id": notification.id}
    except AppError as error:
        await session.rollback()
        raise _terminal_error(error) from error
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


GOVERNED_TOOL_EXECUTORS: dict[str, ToolExecutor] = {
    "create_hr_case": execute_create_hr_case,
    "assign_case_owner": execute_assign_case_owner,
    "update_case_status": execute_update_case_status,
    "send_case_notification": execute_send_case_notification,
    "create_work_task": execute_create_work_task,
}
