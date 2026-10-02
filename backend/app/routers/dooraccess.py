"""门禁管理接口：维护门禁记录，覆盖续期授权、记录闯入、修复门禁等动作。

写操作一律要求请求头携带操作人身份：
- X-Operator-Name：操作人姓名
- X-Operator-Role：角色（门禁管理员 / 只读）
- X-Operator-Site：操作人所属站点
"""
from __future__ import annotations

from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dooraccess import DooraccessService, Operator

router = APIRouter(prefix="/api/dooraccess", tags=["门禁管理"])

service = DooraccessService()

LIST_FIELDS = ["门禁编号", "所属站点", "开门方式", "进出人员", "进出时间", "授权状态", "异常记录", "门禁状态"]
STATUSES = ["正常", "授权过期", "非法闯入", "已修复"]


def _operator(
    x_operator_name: str | None,
    x_operator_role: str | None,
    x_operator_site: str | None,
) -> Operator:
    # 头部只能放拉丁字符，中文身份按 URL 编码传输，这里解回
    return Operator(
        name=unquote(x_operator_name or "").strip(),
        role=unquote(x_operator_role or "").strip(),
        site=unquote(x_operator_site or "").strip(),
    )


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按门禁编号检索"),
    status: str | None = Query(default=None, description="正常、授权过期、非法闯入、已修复"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按门禁编号与状态过滤门禁管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条门禁记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"门禁记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(
    payload: EntryPayload,
    x_operator_name: str | None = Header(default=None),
    x_operator_role: str | None = Header(default=None),
    x_operator_site: str | None = Header(default=None),
) -> ActionResult:
    """登记一条门禁记录：只有本站点的门禁管理员能登记，缺字段或缺授权都说明原因。"""
    operator = _operator(x_operator_name, x_operator_role, x_operator_site)
    entry, missing, denial = service.create_entry(payload.values, operator)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    if denial:
        return ActionResult(ok=False, message=denial)
    return ActionResult(ok=True, message="门禁记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    x_operator_name: str | None = Header(default=None),
    x_operator_role: str | None = Header(default=None),
    x_operator_site: str | None = Header(default=None),
) -> ActionResult:
    """对单条门禁记录执行续期授权、记录闯入、修复门禁。

    只有记录所属站点的门禁管理员能执行；越权、撞单、重复提交都会被打回或幂等吸收，
    并写明原因。客户端可传「请求编号」保证重复提交只落一次。
    """
    action = str(payload.values.get("action") or "").strip()
    request_id = str(payload.values.get("请求编号") or "").strip()
    operator = _operator(x_operator_name, x_operator_role, x_operator_site)
    entry, message = service.run_action(entry_id, action, operator, request_id=request_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出门禁管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dooraccess", "total": total, "items": items}
