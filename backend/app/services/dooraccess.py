"""门禁管理业务规则：状态流转、字段校验与筛选口径都收在这里。

归属与权限约定：
- 每条记录挂在登记时的站点上，所属站点与责任人一经登记不再改写；
- 记录闯入、续期授权、修复门禁三个动作只放行本站点的门禁管理员；
- 只读身份只能查看，任何写操作一律打回，并写明缺的是哪一项授权；
- 修复是终态：续期与修复撞单时按修复算；
- 续期重复提交只落一次，续期时间只在首次续期时写入。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.store import store

MODULE = "dooraccess"
REQUIRED_FIELDS = ["门禁编号", "所属站点", "开门方式"]
STATUS_ORDER = ["正常", "授权过期", "非法闯入", "已修复"]
ACTION_RULES = {"记录闯入": "非法闯入", "续期授权": "正常", "修复门禁": "已修复"}
NEGATIVE_ACTIONS = ["记录闯入"]
FINAL_STATUS = "已修复"

ROLE_ADMIN = "门禁管理员"
ROLE_READONLY = "只读"

# 动作 → 授权项名称：打回时写明缺的是哪一项授权
ACTION_PERMISSIONS = {
    "记录闯入": "登记闯入权限",
    "续期授权": "续期授权权限",
    "修复门禁": "修复门禁权限",
}
CREATE_PERMISSION = "登记门禁权限"

# 动作执行串行化：续期连点、续期与修复撞单时按到达顺序落库，避免并发双写
_action_lock = threading.Lock()


@dataclass(frozen=True)
class Operator:
    """一次请求的操作人身份，由请求头解析而来。"""

    name: str = ""
    role: str = ""
    site: str = ""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _permission_denied(permission: str, site: str, operator: Operator) -> str:
    """统一打回口径：写明缺的是哪一项授权、差在哪一环。"""
    if not operator.name:
        return f"缺少授权：站点「{site}」的{permission}（未登记操作人身份）"
    if operator.role != ROLE_ADMIN:
        role = operator.role or "未登记角色"
        return (
            f"缺少授权：站点「{site}」的{permission}"
            f"（当前身份是{role}，只读身份只能查看，不能执行写操作）"
        )
    return (
        f"缺少授权：站点「{site}」的{permission}"
        f"（当前身份是站点「{operator.site or '未登记'}」的门禁管理员，不能替别的站点代劳）"
    )


class DooraccessService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("门禁编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return [self._normalize(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._normalize(entry) if entry is not None else None

    def create_entry(
        self, values: dict[str, Any], operator: Operator
    ) -> tuple[dict[str, Any] | None, list[str], str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing, ""
        site = str(values.get("所属站点") or "").strip()
        denial = self._check(operator, CREATE_PERMISSION, site)
        if denial:
            return None, [], denial
        with _action_lock:
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = False
            entry["abnormal"] = False
            # 归属落到人：责任人与所属站点自登记起不再改写，后续经手只追加留痕
            entry["责任人"] = operator.name
            entry["最近经手人"] = operator.name
            entry["处理记录"] = [
                {"时间": _now(), "动作": "登记", "经手人": operator.name, "结果状态": entry["status"]}
            ]
            entry["已处理请求"] = []
            rows.append(entry)
        return entry, [], ""

    def run_action(
        self, entry_id: int, action: str, operator: Operator, request_id: str = ""
    ) -> tuple[dict[str, Any] | None, str]:
        with _action_lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"门禁记录 {entry_id} 不存在或已归档"
            self._normalize(entry)
            if action not in ACTION_RULES:
                return None, f"动作「{action}」不属于门禁管理可执行范围"
            site = str(entry.get("所属站点") or "")
            # 幂等：同一请求编号只落一次，重复提交直接回首次结果
            if request_id and request_id in entry["已处理请求"]:
                return entry, f"门禁记录已{action}（重复提交，本次不再写入）"
            denial = self._check(operator, ACTION_PERMISSIONS[action], site)
            if denial:
                return None, denial
            # 修复是终态：续期与修复撞在一起时按修复算
            if entry["status"] == FINAL_STATUS:
                if action == "修复门禁":
                    return entry, "门禁记录已修复（重复提交，本次不再写入）"
                return None, f"门禁记录已修复，修复结果优先，「{action}」不再生效"
            if action == "续期授权":
                if entry.get("续期时间"):
                    return entry, "授权已续期，重复提交不再写入续期时间"
                if entry["status"] != "授权过期":
                    return None, f"当前状态「{entry['status']}」无需续期，仅授权过期的记录可以续期授权"
                entry["续期时间"] = _now()
                entry["续期人"] = operator.name
                entry["授权状态"] = "已续期"
            target = ACTION_RULES[action]
            entry["status"] = target
            entry["pending"] = target in ("授权过期", "非法闯入")
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            # 换人留痕：只追加处理记录、更新最近经手人，责任人与所属站点不动
            entry["处理记录"].append(
                {"时间": _now(), "动作": action, "经手人": operator.name, "结果状态": target}
            )
            entry["最近经手人"] = operator.name
            if request_id:
                entry["已处理请求"].append(request_id)
            return entry, f"门禁记录已{action}"

    def _check(self, operator: Operator, permission: str, site: str) -> str | None:
        """放行本站点的门禁管理员；其余一律打回并写明缺哪一项授权。"""
        if operator.name and operator.role == ROLE_ADMIN and operator.site == site:
            return None
        return _permission_denied(permission, site, operator)

    def _normalize(self, entry: dict[str, Any]) -> dict[str, Any]:
        """老数据补齐归属与留痕字段，保证列表和详情读到的是同一份归属。"""
        entry.setdefault("责任人", "")
        entry.setdefault("最近经手人", entry.get("责任人", ""))
        entry.setdefault("处理记录", [])
        entry.setdefault("已处理请求", [])
        return entry
