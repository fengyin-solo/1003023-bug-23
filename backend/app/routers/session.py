"""会话接口：向前端提供演示用操作员名单，便于切换身份验证归属规则。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import OPERATORS, Operator, current_operator

router = APIRouter(prefix="/api/session", tags=["会话"])


@router.get("")
def get_session(operator: Operator = Depends(current_operator)) -> dict:
    """返回全部可选操作员以及当前请求所使用的身份。"""
    return {
        "current": operator.as_dict(),
        "operators": [op.as_dict() for op in OPERATORS.values()],
    }
