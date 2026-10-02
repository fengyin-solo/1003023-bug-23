<template>
  <section class="page" data-module="dooraccess">
    <header class="page-head">
      <div>
        <h2>门禁管理管理</h2>
        <p class="page-desc">维护门禁记录，围绕门禁编号、所属站点、开门方式、进出人员做登记、筛选与状态流转。</p>
      </div>
      <div class="page-actions">
        <button
          class="btn primary"
          type="button"
          :disabled="!session.canOperate"
          :title="session.canOperate ? '' : '只读身份只能查看，不能登记'"
          @click="openCreate"
        >
          登记门禁记录
        </button>
        <button class="btn" type="button" @click="exportRows">导出门禁管理清单</button>
      </div>
    </header>

    <div class="identity-bar">
      <span class="identity-label">当前身份</span>
      <label class="identity-item">
        <span>角色</span>
        <select :value="session.role" @change="onRoleChange">
          <option value="门禁管理员">门禁管理员</option>
          <option value="只读">只读</option>
        </select>
      </label>
      <label class="identity-item">
        <span>所属站点</span>
        <select :value="session.site" @change="onSiteChange">
          <option v-for="site in siteOptions" :key="site" :value="site">{{ site }}</option>
        </select>
      </label>
      <span v-if="!session.canOperate" class="identity-hint">只读身份只能查看，动作与登记已禁用</span>
    </div>

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
          <th>责任人</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>{{ row['责任人'] || '—' }}</td>
            <td class="row-actions">
              <button
                v-for="action in actions"
                :key="action"
                class="link"
                type="button"
                :disabled="!session.canOperate || pendingAction !== ''"
                :title="session.canOperate ? '' : '只读身份只能查看'"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
              <button class="link" type="button" @click="toggleDetail(row)">
                {{ expandedId === row.id ? '收起' : '详情' }}
              </button>
            </td>
          </tr>
          <tr v-if="expandedId === row.id" class="detail-row">
            <td :colspan="columns.length + 2">
              <div class="detail-panel">
                <p>所属站点：{{ row['所属站点'] }}（记录始终挂在登记站点，不随经手人变更）</p>
                <p>责任人：{{ row['责任人'] || '—' }}　最近经手人：{{ row['最近经手人'] || '—' }}</p>
                <p v-if="row['续期时间']">续期时间：{{ row['续期时间'] }}　续期人：{{ row['续期人'] || '—' }}</p>
                <div>
                  <span>处理留痕：</span>
                  <ul v-if="traceOf(row).length" class="trace-list">
                    <li v-for="(trace, index) in traceOf(row)" :key="index">
                      {{ trace['时间'] }} · {{ trace['动作'] }} · 经手人 {{ trace['经手人'] }} → {{ trace['结果状态'] }}
                    </li>
                  </ul>
                  <span v-else>暂无处理记录</span>
                </div>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无门禁管理数据，可先登记门禁记录</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条门禁管理记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type Trace = Record<string, string>
type Row = Record<string, string | number | null | Trace[]>

const ENDPOINT = '/api/dooraccess'
const columns = ["门禁编号", "所属站点", "开门方式", "进出人员", "进出时间", "授权状态", "异常记录", "门禁状态"]
const actions = ["续期授权", "记录闯入", "修复门禁"]
const statuses = ["正常", "授权过期", "非法闯入", "已修复"]
const stats = [{"label": "正常门禁", "value": 0}, {"label": "过期门禁", "value": 0}, {"label": "闯入记录", "value": 0}]

const session = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const expandedId = ref<string | number | null>(null)
const pendingAction = ref('')

const siteOptions = computed(() => {
  const sites = new Set<string>()
  for (const row of rows.value) {
    const site = row['所属站点']
    if (typeof site === 'string' && site) sites.add(site)
  }
  if (session.site) sites.add(session.site)
  return Array.from(sites)
})

function onRoleChange(event: Event) {
  session.setIdentity({ role: (event.target as HTMLSelectElement).value })
}

function onSiteChange(event: Event) {
  session.setIdentity({ site: (event.target as HTMLSelectElement).value })
}

function traceOf(row: Row): Trace[] {
  const traces = row['处理记录']
  return Array.isArray(traces) ? (traces as Trace[]) : []
}

function toggleDetail(row: Row) {
  expandedId.value = expandedId.value === row.id ? null : (row.id as string | number)
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '门禁记录登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  if (pendingAction.value) return
  errorMessage.value = ''
  noticeMessage.value = ''
  pendingAction.value = action
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, 请求编号: crypto.randomUUID() }),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) {
      throw new Error(result.message || '门禁管理动作未生效，请稍后重试')
    }
    noticeMessage.value = result.message || `门禁记录已${action}`
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '门禁管理操作失败'
  } finally {
    pendingAction.value = ''
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
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
.identity-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 13px;
}
.identity-label { color: var(--muted); }
.identity-item { display: flex; align-items: center; gap: 6px; }
.identity-item span { color: var(--muted); font-size: 12px; }
.identity-hint { color: #b42318; font-size: 12px; }
.link:disabled { color: var(--muted); cursor: not-allowed; }
.detail-row td { background: #f8fafc; }
.detail-panel { font-size: 13px; }
.detail-panel p { margin: 4px 0; }
.trace-list { margin: 4px 0; padding-left: 18px; }
.notice-text { color: #067647; }
</style>
