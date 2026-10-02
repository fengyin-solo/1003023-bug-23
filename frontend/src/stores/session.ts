import { defineStore } from 'pinia'

export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: '值班管理员',
    role: '门禁管理员',
    site: '门禁管理样例1',
    shiftLabel: '白班 08:00-20:00',
    scope: '通信基站运维管理平台',
  }),
  getters: {
    // 只有门禁管理员能执行写操作；只读身份只能查看
    canOperate: (state) => state.role === '门禁管理员' && state.operator.length > 0,
  },
  actions: {
    setShift(label: string) {
      this.shiftLabel = label
    },
    setIdentity(identity: { operator?: string; role?: string; site?: string }) {
      if (identity.operator !== undefined) this.operator = identity.operator
      if (identity.role !== undefined) this.role = identity.role
      if (identity.site !== undefined) this.site = identity.site
    },
  },
})
