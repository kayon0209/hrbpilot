"""Contract tests for the network-isolated WeCom outbound simulator."""

import pytest

from app.connectors.wecom_outbound import WeComOutboundSimulator


@pytest.mark.asyncio
async def test_simulator_returns_wecom_style_success_with_msgid() -> None:
    simulator = WeComOutboundSimulator()

    token = await simulator.get_token("simulated-corp", "local-only")
    response = await simulator.send_text(token.value, "1000002", "employee-a", "请补充材料")

    assert response.errcode == 0
    assert response.errmsg == "ok"
    assert response.msgid is not None and response.msgid.startswith("sim-wecom-")
    assert response.invaliduser is None
    assert response.retryable is False


@pytest.mark.asyncio
async def test_simulator_marks_invalid_user_terminal_and_timeout_retryable() -> None:
    invalid_user_gateway = WeComOutboundSimulator(invalid_users={"invalid-user"})
    invalid_token = await invalid_user_gateway.get_token("simulated-corp", "local-only")
    invalid = await invalid_user_gateway.send_text(
        invalid_token.value, "1000002", "invalid-user", "请补充材料"
    )
    assert invalid.errcode == 60111
    assert invalid.invaliduser == "invalid-user"
    assert invalid.retryable is False

    timeout_gateway = WeComOutboundSimulator(fault_mode="timeout")
    timeout_token = await timeout_gateway.get_token("simulated-corp", "local-only")
    timeout = await timeout_gateway.send_text(
        timeout_token.value, "1000002", "employee-a", "请补充材料"
    )
    assert timeout.errcode == -1
    assert timeout.retryable is True


@pytest.mark.asyncio
async def test_simulator_rejects_non_sendable_inputs_without_network_io() -> None:
    simulator = WeComOutboundSimulator()
    with pytest.raises(ValueError, match="corp_id"):
        await simulator.get_token("", "local-only")

    token = await simulator.get_token("simulated-corp", "local-only")
    with pytest.raises(ValueError, match="touser"):
        await simulator.send_text(token.value, "1000002", "other-corp/employee", "x")
    with pytest.raises(ValueError, match="agent_id"):
        await simulator.send_text(token.value, "agent", "employee-a", "x")
