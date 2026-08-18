"""Milvus scalar filters must reject expression injection."""

import pytest

from app.rag.storage.milvus import validate_filter_id
from app.shared.errors import ValidationError


@pytest.mark.parametrize("value", ["tenant-a", "policy_kb", "3fa85f64-5717-4562-b3fc-2c963f66afa6"])
def test_validate_filter_id_accepts_project_identifiers(value: str) -> None:
    assert validate_filter_id(value) == value


@pytest.mark.parametrize("value", ["", "x\" or true", "a && b", "../tenant", "a b"])
def test_validate_filter_id_rejects_expression_syntax(value: str) -> None:
    with pytest.raises(ValidationError, match="Invalid Milvus filter identifier"):
        validate_filter_id(value)
