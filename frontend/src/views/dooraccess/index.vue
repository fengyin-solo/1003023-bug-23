<template>
  <section class="page" data-module="dooraccess">
    <header class="page-head">
      <div>
        <h2>门禁管理管理</h2>
        <p class="page-desc">维护门禁记录，围绕门禁编号、所属站点、开门方式、进出人员做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="exportRows">导出门禁管理清单</button>
      </div>
    </header>

    <p class="auth-banner" v-if="!session.isDoorAdmin">
      当前身份「{{ session.operator.name }}」为只读账号，仅可查看门禁列表与详情；登记闯入、续期授权、修复均需本站点门禁管理员。
    </p>
    <p class="auth-banner ok" v-else>
      当前身份「{{ session.operator.name }}」是{{ session.operator.sites.join('、') }}的门禁管理员；其他站点的记录只能查看，代操作会被打回。
    </p>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <template v-for="action in actions" :key="action">
              <button
                v-if="canRun(action, row)"
                class="link"
                type="button"
                :disabled="busyKey === actionKey(action, row)"
                @click="runAction(action, row)"
              >
                {{ busyKey === actionKey(action, row) ? '处理中…' : action }}
              </button>
              <span v-else class="link disabled" :title="denyReason(action, row)">{{ action }}</span>
            </template>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无门禁管理数据</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条门禁管理记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 详情抽屉：只读身份看到的表单全部禁用 -->
    <div v-if="detail" class="drawer-mask" @click.self="closeDetail">
      <div class="drawer">
        <header class="drawer-head">
          <h3>门禁记录 {{ detail['门禁编号'] }}</h3>
          <button class="btn ghost" type="button" @click="closeDetail">关闭</button>
        </header>

        <div class="detail-site-line">
          所属站点：<strong>{{ detail['所属站点'] }}</strong>
          <span class="muted">（站点归属不可修改）</span>
          · 当前责任人：<strong>{{ detail['责任人'] || '未指派' }}</strong>
        </div>

        <p v-if="!detailEditable" class="auth-banner">
          只读身份或非本站点管理员只能查看，开门方式与授权状态均不可修改。
        </p>

        <form class="detail-form" @submit.prevent="saveDetail">
          <label>
            <span>开门方式</span>
            <input v-model="editForm['开门方式']" :disabled="!detailEditable" />
          </label>
          <label>
            <span>进出人员</span>
            <input v-model="editForm['进出人员']" :disabled="!detailEditable" />
          </label>
          <label>
            <span>异常记录</span>
            <input v-model="editForm['异常记录']" :disabled="!detailEditable" />
          </label>
          <label>
            <span>授权状态（仅由续期动作流转）</span>
            <input :value="detail['授权状态']" disabled />
          </label>
          <label>
            <span>交接责任人（限本站点管理员接手）</span>
            <select v-model="editForm['责任人']" :disabled="!detailEditable">
              <option v-for="name in assignableOwners" :key="name" :value="name">{{ name }}</option>
            </select>
          </label>
          <div class="detail-actions">
            <button class="btn primary" type="submit" :disabled="!detailEditable">保存修改 / 交接</button>
            <span v-if="detailError" class="error-text">{{ detailError }}</span>
          </div>
        </form>

        <h4>操作留痕</h4>
        <ol class="trace-list">
          <li v-for="(trace, idx) in (detail.history || [])" :key="idx">
            <span class="trace-time">{{ trace['时间'] }}</span>
            <span class="trace-action">{{ trace['动作'] }}</span>
            <span class="trace-status">{{ trace['原状态'] ?? '—' }} → {{ trace['新状态'] ?? '—' }}</span>
            <span class="trace-who">{{ trace['经手人'] ? `经手人：${trace['经手人']}` : '' }}</span>
            <p v-if="trace['说明']" class="trace-detail">{{ trace['说明'] }}</p>
          </li>
        </ol>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Row = Record<string, string | number | boolean | null | string[] | Record<string, unknown>>

const ENDPOINT = '/api/dooraccess'
const columns = ["门禁编号", "所属站点", "责任人", "开门方式", "进出人员", "进出时间", "授权状态", "最近续期时间", "异常记录", "门禁状态"]
const actions = ["续期授权", "记录闯入", "修复门禁"]

const session = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = ["门禁编号", "所属站点"]
const busyKey = ref('')
// 同一条记录同一动作的在途请求：防止双击发两次（续期的第二道保险）
const inflight = new Set<string>()
// 每次进入页面/重置后，「一条记录在某一过期周期内的一次续期意图」只生成一个幂等键；
// 键带状态：将来该记录真的再次过期（新周期），会拿到新键，不会被误判成重复提交。
// 状态尚未刷新的连点窗口由 inflight 在途锁兜底，两者叠加保证双击只发一份。
const renewalKeys = new Map<string, string>()

const stats = computed(() => [
  { label: "正常门禁", value: rows.value.filter((r) => r.status === '正常').length },
  { label: "过期门禁", value: rows.value.filter((r) => r.status === '授权过期').length },
  { label: "闯入记录", value: rows.value.filter((r) => r.status === '非法闯入').length },
  { label: "已修复", value: rows.value.filter((r) => r.status === '已修复').length },
])

// --------------------------------------------------------------- 详情抽屉
const detail = ref<Row | null>(null)
const detailError = ref('')
const editForm = reactive<Record<string, string>>({
  开门方式: '',
  进出人员: '',
  异常记录: '',
  责任人: '',
})

const detailEditable = computed(() =>
  !!detail.value && session.canAdminSite(detail.value['所属站点'] as string),
)

const assignableOwners = computed(() => {
  if (!detail.value) return []
  const site = detail.value['所属站点'] as string
  return session.operators
    .filter((op) => op.role === '门禁管理员' && op.sites.includes(site))
    .map((op) => op.name)
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  detailError.value = ''
  const response = await request(`${ENDPOINT}/${row.id}`)
  if (!response.ok) {
    errorMessage.value = '门禁详情读取失败'
    return
  }
  const payload = await response.json()
  const entry: Row = payload
  detail.value = entry
  editForm['开门方式'] = String(entry['开门方式'] ?? '')
  editForm['进出人员'] = String(entry['进出人员'] ?? '')
  editForm['异常记录'] = String(entry['异常记录'] ?? '')
  editForm['责任人'] = String(entry['责任人'] ?? '')
}

function closeDetail() {
  detail.value = null
  detailError.value = ''
}

async function saveDetail() {
  const current = detail.value
  if (!current) return
  detailError.value = ''
  try {
    const response = await request(`${ENDPOINT}/${current.id}`, {
      method: 'PATCH',
      headers: session.header as Record<string, string>,
      body: JSON.stringify({ values: { ...editForm } }),
    })
    const payload = await response.json()
    if (!response.ok) {
      detailError.value = payload.detail || '保存未生效'
      return
    }
    detail.value = payload.entry
    await reload()
    closeDetail()
  } catch (error) {
    detailError.value = error instanceof Error ? error.message : '门禁详情保存失败'
  }
}

// --------------------------------------------------------------- 动作授权
function canRun(action: string, row: Row): boolean {
  return session.canAdminSite(row['所属站点'] as string)
}

function denyReason(action: string, row: Row): string {
  if (!session.isDoorAdmin) {
    return `只读身份不能执行「${action}」`
  }
  return `「${session.operator.name}」不是「${row['所属站点']}」的门禁管理员，缺少该站点授权`
}

function actionKey(action: string, row: Row): string {
  return `${row.id}:${action}`
}

function idempotencyKeyFor(row: Row): string {
  const id = Number(row.id)
  const statusKey = String(row.status ?? '')
  const cacheKey = `${id}:${statusKey}`
  let key = renewalKeys.get(cacheKey)
  if (!key) {
    key = `renew-${id}-${statusKey}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    renewalKeys.set(cacheKey, key)
  }
  return key
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  const key = actionKey(action, row)
  if (inflight.has(key)) {
    // 连点第二下：第一下还在途，直接丢弃，续期不会写第二回
    return
  }
  inflight.add(key)
  busyKey.value = key
  try {
    const values: Record<string, string> = { action }
    if (action === '续期授权') {
      values.idempotency_key = idempotencyKeyFor(row)
    }
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      headers: session.header as Record<string, string>,
      body: JSON.stringify({ values }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(payload?.detail || '门禁管理动作未生效，请稍后重试')
    }
    errorMessage.value = payload?.message ? `已处理：${payload.message}` : ''
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '门禁管理操作失败'
  } finally {
    inflight.delete(key)
    busyKey.value = ''
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}/?${query}`)
    if (!response.ok) {
      throw new Error('门禁记录列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '门禁管理列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.auth-banner {
  margin: 8px 0;
  padding: 8px 12px;
  border-radius: 6px;
  background: #fef3c7;
  border: 1px solid #fde68a;
  color: #92400e;
  font-size: 13px;
}
.auth-banner.ok {
  background: #ecfdf5;
  border-color: #a7f3d0;
  color: #065f46;
}
.link.disabled {
  color: #98a2b3;
  cursor: not-allowed;
}
.muted {
  color: #98a2b3;
  font-size: 12px;
}
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  justify-content: flex-end;
  z-index: 50;
}
.drawer {
  width: 520px;
  max-width: 92vw;
  height: 100%;
  background: #fff;
  padding: 16px 20px;
  overflow-y: auto;
}
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.detail-site-line {
  font-size: 13px;
  margin: 8px 0 12px;
}
.detail-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.detail-form label span {
  display: block;
  font-size: 12px;
  color: #667085;
  margin-bottom: 2px;
}
.detail-form input,
.detail-form select {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
}
.detail-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}
.trace-list {
  margin-top: 8px;
  padding-left: 18px;
  font-size: 12px;
}
.trace-list li {
  margin-bottom: 8px;
}
.trace-time {
  color: #667085;
  margin-right: 8px;
}
.trace-action {
  font-weight: 600;
  margin-right: 8px;
}
.trace-status {
  color: #1d4ed8;
  margin-right: 8px;
}
.trace-who {
  color: #475467;
}
.trace-detail {
  margin: 2px 0 0;
  color: #667085;
}
</style>
