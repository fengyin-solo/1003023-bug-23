"""门禁管理接口：维护门禁记录，覆盖续期授权、记录闯入、修复门禁等动作。

归属判断在服务层完成；路由层负责把「未授权 / 状态冲突」翻译成对应的 HTTP 状态码。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth import Operator, current_operator
from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.dooraccess import MODULE, DooraccessError, DooraccessService

router = APIRouter(prefix="/api/dooraccess", tags=["门禁管理"])

service = DooraccessService()

LIST_FIELDS = ["门禁编号", "所属站点", "责任人", "开门方式", "进出人员", "进出时间", "授权状态", "最近续期时间", "异常记录", "门禁状态"]
STATUSES = ["正常", "授权过期", "非法闯入", "已修复"]


def _raise_for(error: DooraccessError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.message)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按门禁编号检索"),
    status: str | None = Query(default=None, description="正常、授权过期、非法闯入、已修复"),
    site: str | None = Query(default=None, description="按所属站点过滤"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按门禁编号、状态与站点过滤门禁管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, site=site, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条门禁记录明细（含归属责任人与操作留痕）；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"门禁记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条门禁记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="门禁记录已登记", entry=entry)


@router.patch("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload, operator: Operator = Depends(current_operator)) -> ActionResult:
    """修改门禁详情 / 交接责任人。只读身份只能看；非本站点管理员一律打回；所属站点不可改。"""
    try:
        entry = service.update_entry(entry_id, payload.values, operator)
    except DooraccessError as error:
        _raise_for(error)
    return ActionResult(ok=True, message="门禁详情已更新，变更已记入操作留痕", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(
    entry_id: int,
    payload: EntryPayload,
    operator: Operator = Depends(current_operator),
) -> ActionResult:
    """对单条门禁记录执行续期授权、记录闯入、修复门禁。

    - 只读账号、非本站点管理员：403 并说明缺少哪一项授权；
    - 续期重复提交：同一幂等键只落一次；
    - 续期与修复冲突：修复优先，续期返回 409。
    """
    action = str(payload.values.get("action") or "").strip()
    idem_key = payload.values.get("idempotency_key")
    try:
        entry, message = service.run_action(
            entry_id, action, operator, idempotency_key=str(idem_key) if idem_key is not None else None
        )
    except DooraccessError as error:
        _raise_for(error)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/export")
def export_entries(operator: Operator = Depends(current_operator)) -> dict[str, Any]:
    """导出门禁管理清单：返回全量数据（任何身份可查看）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": MODULE, "total": total, "items": items, "operator": operator.as_dict()}
