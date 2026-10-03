"""门禁管理业务规则：归属授权、状态流转、字段校验、换人留痕与续期幂等都收在这里。

设计要点（对应历次暴露的问题）：

- 三件事（记录闯入、续期授权、修复门禁）只认「本站点门禁管理员」，
  只读身份和别的站点的账号一律打回，并写明少的是哪一项授权；
- 门禁记录一旦建立，所属站点不可修改；换人只换责任人并留痕，
  列表和详情从同一份记录取归属，不会再对不上；
- 修复是最高优先级的终态：已修复的记录不能再被续期改回正常，
  「续期跟修复撞在一起」最终一定按修复算；
- 续期按幂等键去重，同一份授权连点两次只落一次，续期时间不重写。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.auth import Operator, require_door_admin
from app.store import store

MODULE = "dooraccess"
REQUIRED_FIELDS = ["门禁编号", "所属站点", "开门方式"]
STATUS_ORDER = ["正常", "授权过期", "非法闯入", "已修复"]
FINAL_STATUS = "已修复"
RENEW_STATUS = "正常"
ACTION_RULES = {"续期授权": "正常", "记录闯入": "非法闯入", "修复门禁": "已修复"}

# 只允许通过详情修改的业务字段；授权状态、所属站点不在其列
EDITABLE_FIELDS = ("开门方式", "进出人员", "异常记录")


class DooraccessError(Exception):
    """门禁业务规则被违反（记录不存在、状态不允许等）。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class DooraccessService:
    def __init__(self) -> None:
        # 三件事会改同一条记录，串行化避免续期与修复并发时互相覆盖
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ 查询
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        site: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("门禁编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if site:
            rows = [row for row in rows if row.get("所属站点") == site]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    # ------------------------------------------------------------------ 登记
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["进出人员"] = str(values.get("进出人员") or "").strip()
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["授权状态"] = "有效"
        entry["最近续期时间"] = None
        entry["责任人"] = None
        entry["history"] = [self._trace("登记门禁", None, STATUS_ORDER[0], None)]
        rows.append(entry)
        return entry, []

    # ------------------------------------------------------------ 详情修改/换人
    def update_entry(self, entry_id: int, values: dict[str, Any], operator: Operator) -> dict[str, Any]:
        """只读身份只能看；开门方式等字段与换人都要求本站点门禁管理员。

        所属站点永不允许修改，授权状态只能走「续期授权」动作，保证归属不漂移。
        """
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise DooraccessError(f"门禁记录 {entry_id} 不存在或已归档", 404)

        denied = require_door_admin(operator, entry.get("所属站点"))
        if denied:
            raise DooraccessError(denied, 403)

        with self._lock:
            # 先做全部硬性校验，任何一项不过都不落库，避免半成功状态
            if "所属站点" in values and str(values["所属站点"] or "").strip() != str(entry.get("所属站点") or ""):
                raise DooraccessError("门禁记录的所属站点不可修改；换人不换站，记录仍挂在原站点下", 422)
            if "授权状态" in values:
                raise DooraccessError("授权状态不允许直接修改，请通过「续期授权」动作办理", 422)

            new_owner_name: str | None = None
            if values.get("责任人") is not None and str(values["责任人"]).strip() != str(entry.get("责任人") or ""):
                new_owner_name = str(values["责任人"]).strip()
                if not new_owner_name:
                    raise DooraccessError("接手责任人不能为空", 422)
                # 换人是本站点管理员把记录交给本站点的人；跨站点交接不允许
                if not any(new_owner_name == op.name for op in _admins_of_site(str(entry.get("所属站点")))):
                    raise DooraccessError(
                        f"「{new_owner_name}」不是「{entry.get('所属站点')}」的门禁管理员，不能接手该站点的门禁记录",
                        422,
                    )

            changes: list[str] = []
            for field in EDITABLE_FIELDS:
                if field in values and str(values.get(field) or "") != str(entry.get(field) or ""):
                    new_value = str(values.get(field) or "").strip()
                    changes.append(f"{field}：{entry.get(field) or '—'} → {new_value or '—'}")
                    entry[field] = new_value

            if new_owner_name is not None:
                changes.append(f"责任人：{entry.get('责任人') or '未指派'} → {new_owner_name}")
                entry["责任人"] = new_owner_name
            if changes:
                action_name = "交接责任人" if new_owner_name is not None else "修改详情"
                entry["history"].append(
                    self._trace(action_name, entry.get("status"), entry.get("status"), operator, detail="；".join(changes))
                )

            return entry

    # ------------------------------------------------------------------ 动作
    def run_action(
        self,
        entry_id: int,
        action: str,
        operator: Operator,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            raise DooraccessError(f"门禁记录 {entry_id} 不存在或已归档", 404)
        if action not in ACTION_RULES:
            raise DooraccessError(f"动作「{action}」不属于门禁管理可执行范围")

        # 归属优先：不是本站点门禁管理员，后面的状态判断一律不给看
        denied = require_door_admin(operator, entry.get("所属站点"))
        if denied:
            raise DooraccessError(denied, 403)

        with self._lock:
            if action == "续期授权":
                return self._renew(entry, operator, (idempotency_key or "").strip())
            if action == "记录闯入":
                return self._mark_intrusion(entry, operator)
            return self._repair(entry, operator)

    # ------------------------------------------------------------- 三件事细则
    def _renew(self, entry: dict[str, Any], operator: Operator, idem_key: str) -> tuple[dict[str, Any], str]:
        """续期授权：幂等键去重 + 修复终态优先 + 只在「授权过期」时受理。"""
        # 同一个幂等键第二次提交：原结果原样返回，不再写第二回
        seen = entry.setdefault("renewal_keys", {})
        if idem_key and idem_key in seen:
            return entry, "该续期申请已处理，重复提交未再写入（授权状态与续期时间保持第一次的结果）"

        if entry.get("status") == FINAL_STATUS:
            # 续期跟修复撞在一起，按修复算：续期不能把已修复的记录改回正常
            raise DooraccessError("该门禁已修复，修复优先于续期，续期不再受理", 409)
        if entry.get("status") != "授权过期":
            raise DooraccessError("只有「授权过期」的门禁记录可以续期，当前授权仍有效，无需重复续期", 409)

        self._set_status(entry, RENEW_STATUS)
        entry["授权状态"] = "已续期"
        renewed_at = _now()
        entry["最近续期时间"] = renewed_at
        if idem_key:
            seen[idem_key] = renewed_at
        entry["history"].append(self._trace("续期授权", "授权过期", RENEW_STATUS, operator))
        return entry, f"门禁授权已续期，续期时间 {renewed_at}"

    def _mark_intrusion(self, entry: dict[str, Any], operator: Operator) -> tuple[dict[str, Any], str]:
        """登记闯入：已修复的不能再翻案，已在案的不重复登记。"""
        if entry.get("status") == FINAL_STATUS:
            raise DooraccessError("该门禁已修复，闯入记录须另行登记新事件，不能改动已修复记录", 409)
        if entry.get("status") == "非法闯入":
            raise DooraccessError("该门禁已登记为非法闯入，请勿重复登记", 409)
        old_status = entry.get("status")
        self._set_status(entry, "非法闯入")
        entry["授权状态"] = "冻结"
        entry["异常记录"] = f"{_now()} 登记非法闯入"
        entry["history"].append(self._trace("记录闯入", old_status, "非法闯入", operator))
        return entry, "已登记非法闯入"

    def _repair(self, entry: dict[str, Any], operator: Operator) -> tuple[dict[str, Any], str]:
        """修复门禁：修复是终态，已修复的不重复处理；与续期并发时修复压过续期。"""
        if entry.get("status") == FINAL_STATUS:
            return entry, "该门禁已是修复状态，无需重复修复"
        old_status = entry.get("status")
        self._set_status(entry, FINAL_STATUS)
        entry["异常记录"] = f"{_now()} 门禁修复完成"
        entry["history"].append(self._trace("修复门禁", old_status, FINAL_STATUS, operator))
        return entry, "门禁已修复（修复优先，同批次的续期不再改写状态）"

    # ------------------------------------------------------------------ 工具
    @staticmethod
    def _set_status(entry: dict[str, Any], status: str) -> None:
        entry["status"] = status
        entry["门禁状态"] = status
        entry["pending"] = status != FINAL_STATUS
        entry["abnormal"] = status in ("授权过期", "非法闯入")

    @staticmethod
    def _trace(
        action: str,
        old_status: Any,
        new_status: Any,
        operator: Operator | None = None,
        *,
        detail: str | None = None,
    ) -> dict[str, Any]:
        trace: dict[str, Any] = {
            "时间": _now(),
            "动作": action,
            "原状态": old_status,
            "新状态": new_status,
        }
        if operator is not None:
            trace["经手人"] = operator.name
            trace["经手人账号"] = operator.id
        if detail:
            trace["说明"] = detail
        return trace


def _admins_of_site(site: str) -> list[Operator]:
    """某站点现任门禁管理员名单（换人时校验接手人用）。"""
    from app.auth import OPERATORS

    return [op for op in OPERATORS.values() if op.can_admin_site(site)]
