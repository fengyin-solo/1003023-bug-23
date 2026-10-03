"""操作员身份与门禁授权口径。

归属判断要落到人：每个请求必须带一个操作员（X-Operator-Id），
操作员有角色（门禁管理员 / 只读）和站点管辖范围。门禁的三件事
——登记闯入、续期授权、修复——只有「本站点的门禁管理员」能做。

演示环境用内存里的固定账号；真实项目这里换成用户中心 / 权限服务。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import Header

ROLE_DOOR_ADMIN = "门禁管理员"
ROLE_VIEWER = "只读"

# 只有门禁管理员角色能做的三件事
DOOR_ADMIN_ACTIONS = ("记录闯入", "续期授权", "修复门禁")


@dataclass(frozen=True)
class Operator:
    """一次请求的操作主体：谁、什么角色、能管哪些站点。"""

    id: str
    name: str
    role: str
    sites: tuple[str, ...]

    @property
    def is_door_admin(self) -> bool:
        return self.role == ROLE_DOOR_ADMIN

    def can_admin_site(self, site: str | None) -> bool:
        return self.is_door_admin and bool(site) and site in self.sites

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "role": self.role, "sites": list(self.sites)}


# 演示账号：覆盖本站点管理员、跨站点管理员、只读三类情形
OPERATORS: dict[str, Operator] = {
    "u-wang": Operator("u-wang", "王站管", ROLE_DOOR_ADMIN, ("滨江东信基站", "云溪路基站")),
    "u-chen": Operator("u-chen", "陈班", ROLE_DOOR_ADMIN, ("云溪路基站",)),
    "u-zhao": Operator("u-zhao", "赵工", ROLE_DOOR_ADMIN, ("临港工业基站",)),
    "u-lin": Operator("u-lin", "林只读", ROLE_VIEWER, ()),
}

# 未显式登录时按只读访客对待：默认什么都改不了，只能看
ANONYMOUS = Operator("anonymous", "未登录访客", ROLE_VIEWER, ())


def operator_from_header(x_operator_id: str | None) -> Operator:
    """按请求头解析操作员；账号不存在时一律降级为只读访客，不发放权限。"""
    if x_operator_id:
        known = OPERATORS.get(x_operator_id.strip())
        if known is not None:
            return known
    return ANONYMOUS


def require_door_admin(operator: Operator, site: str | None) -> str | None:
    """门禁三件事的统一授权口径。

    返回 None 表示放行；否则返回一句可读的拒绝说明，写明缺的是哪一项授权。
    """
    if not operator.is_door_admin:
        return "当前账号为只读身份，门禁的登记闯入、续期授权、修复仅限本站点门禁管理员操作"
    if not site:
        return "门禁记录缺少所属站点，无法核验站点归属"
    if site not in operator.sites:
        return (
            f"账号「{operator.name}」不是「{site}」的门禁管理员"
            f"（其管辖站点为：{('、'.join(operator.sites)) or '无'}），"
            "缺少该站点的门禁管理授权，不能代为操作"
        )
    return None


def current_operator(x_operator_id: str | None = Header(default=None, alias="X-Operator-Id")) -> Operator:
    """FastAPI 依赖：把请求头解析成操作员对象。"""
    return operator_from_header(x_operator_id)
