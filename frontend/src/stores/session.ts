import { defineStore } from 'pinia'

export interface OperatorInfo {
  id: string
  name: string
  role: string
  sites: string[]
}

const STORAGE_KEY = 'ops-operator-id'

/**
 * 会话身份：归属判断要落到人，前端必须明确「当前是谁、什么角色、能管哪些站点」。
 * 未选择时为只读访客：能看不能动。
 */
export const useSessionStore = defineStore('session', {
  state: () => ({
    operator: {
      id: 'anonymous',
      name: '未登录访客',
      role: '只读',
      sites: [] as string[],
    } as OperatorInfo,
    operators: [] as OperatorInfo[],
    shiftLabel: '白班 08:00-20:00',
    scope: '通信基站运维管理平台',
  }),
  getters: {
    isDoorAdmin: (state) => state.operator.role === '门禁管理员',
    /** 请求门禁写接口时要带的身份头；只读访客返回空串即可，后端会按只读处理 */
    header: (state) => ({ 'X-Operator-Id': state.operator.id === 'anonymous' ? '' : state.operator.id }),
    canAdminSite: (state) => (site?: string | null) =>
      state.operator.role === '门禁管理员' && !!site && state.operator.sites.includes(site),
  },
  actions: {
    async loadOperators() {
      const resp = await fetch('/api/session', { headers: this.header })
      if (resp.ok) {
        const data = await resp.json()
        this.operators = data.operators ?? []
        const savedId = window.localStorage.getItem(STORAGE_KEY)
        if (savedId) {
          this.select(savedId)
        }
      }
    },
    select(id: string) {
      const found = this.operators.find((item) => item.id === id)
      if (found) {
        this.operator = found
        window.localStorage.setItem(STORAGE_KEY, id)
      } else {
        this.operator = { id: 'anonymous', name: '未登录访客', role: '只读', sites: [] }
        window.localStorage.removeItem(STORAGE_KEY)
      }
    },
    setShift(label: string) {
      this.shiftLabel = label
    },
  },
})
