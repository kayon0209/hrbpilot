import { type FormEvent, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  type AdminUserView,
  type LegacyWorkView,
  type OrgUnitView,
  assignUserOrgUnit,
  claimLegacyWork,
  createOrgUnit,
  listAdminUsers,
  listLegacyWork,
  replaceManagerScopes,
} from '../../api/admin-users'
import { AsyncState } from '../../components/AsyncState'

const ROLE_LABELS = { employee: '员工', hrbp: 'HRBP', hr_manager: 'HR 经理', admin: '管理员' } as const

export function AdminUsersPage() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['admin-users'], queryFn: listAdminUsers })
  const legacyQuery = useQuery({ queryKey: ['admin-legacy-work'], queryFn: listLegacyWork })
  const [orgName, setOrgName] = useState('')
  const [notice, setNotice] = useState('')
  const [claimNotice, setClaimNotice] = useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: ['admin-users'] })
  const createOrg = useMutation({
    mutationFn: createOrgUnit,
    onSuccess: async created => {
      setOrgName('')
      setNotice(`组织已创建：${created.name}`)
      await refresh()
    },
  })
  const assignOrg = useMutation({
    mutationFn: ({ userId, orgUnitId }: { userId: string; orgUnitId: string | null }) => assignUserOrgUnit(userId, orgUnitId),
    onSuccess: refresh,
  })
  const saveScopes = useMutation({
    mutationFn: ({ managerId, orgUnitIds }: { managerId: string; orgUnitIds: string[] }) => replaceManagerScopes(managerId, orgUnitIds),
    onSuccess: refresh,
  })
  const claimWork = useMutation({
    mutationFn: ({ work, userId }: { work: LegacyWorkView; userId: string }) => claimLegacyWork(work, userId),
    onSuccess: async (_claimed, variables) => {
      setClaimNotice(`历史工作已认领：${variables.work.title}`)
      await queryClient.invalidateQueries({ queryKey: ['admin-legacy-work'] })
    },
  })

  async function submitOrg(event: FormEvent) {
    event.preventDefault()
    const name = orgName.trim()
    if (!name) return
    setNotice('')
    await createOrg.mutateAsync(name)
  }

  return <main className="page-stack">
    <header className="page-heading"><div><span className="eyebrow">管理后台</span><h1>用户与权限</h1><p>核对每位用户的角色与组织归属。业务材料是否可见还取决于明确的对象和组织授权。</p></div></header>
    {query.isPending && <AsyncState kind="loading" title="正在读取用户" />}
    {query.isError && <AsyncState kind="error" title="用户读取失败" detail={query.error.message} action={<button onClick={() => query.refetch()}>重试</button>} />}
    {query.data && <>
      <section className="panel" aria-labelledby="org-unit-heading">
        <h2 id="org-unit-heading">组织单元</h2>
        <p>先建立组织，再给用户分配归属；经理只能查看明确授权范围内的业务对象。</p>
        <form onSubmit={submitOrg}>
          <label>组织名称<input value={orgName} onChange={event => setOrgName(event.target.value)} maxLength={200} required /></label>
          <button type="submit" disabled={createOrg.isPending}>{createOrg.isPending ? '正在创建…' : '创建组织'}</button>
        </form>
        {notice && <p role="status">{notice}</p>}
        {createOrg.isError && <p role="alert">组织创建失败：{createOrg.error.message}</p>}
        {query.data.org_units.length === 0 ? <p>尚未建立组织单元。</p> : <ul>{query.data.org_units.map(org => <li key={org.org_unit_id}>{org.name}</li>)}</ul>}
      </section>

      <section className="panel" aria-labelledby="user-assignment-heading">
        <h2 id="user-assignment-heading">用户归属与经理范围</h2>
        <div className="issue-list">{query.data.users.map(user => <UserAccessCard
          key={user.user_id}
          user={user}
          orgUnits={query.data.org_units}
          assigning={assignOrg.isPending && assignOrg.variables?.userId === user.user_id}
          savingScopes={saveScopes.isPending && saveScopes.variables?.managerId === user.user_id}
          onAssign={orgUnitId => assignOrg.mutate({ userId: user.user_id, orgUnitId })}
          onSaveScopes={orgUnitIds => saveScopes.mutate({ managerId: user.user_id, orgUnitIds })}
        />)}</div>
        {assignOrg.isError && <p role="alert">组织分配失败：{assignOrg.error.message}</p>}
        {saveScopes.isError && <p role="alert">经理范围保存失败：{saveScopes.error.message}</p>}
      </section>

      <section className="panel" aria-labelledby="legacy-work-heading">
        <h2 id="legacy-work-heading">迁移后未归属工作</h2>
        <p>这些旧记录因没有可靠创建者而被安全隐藏。核对事实后逐条认领，不自动猜测负责人。</p>
        {legacyQuery.isPending && <AsyncState kind="loading" title="正在检查历史工作" />}
        {legacyQuery.isError && <AsyncState kind="error" title="历史工作读取失败" detail={legacyQuery.error.message} action={<button onClick={() => legacyQuery.refetch()}>重试</button>} />}
        {claimNotice && <p role="status">{claimNotice}</p>}
        {claimWork.isError && <p role="alert">历史工作认领失败：{claimWork.error.message}</p>}
        {legacyQuery.data && legacyQuery.data.items.length === 0 && <p>没有未归属的历史工作。</p>}
        {legacyQuery.data && legacyQuery.data.items.length > 0 && <div className="issue-list">
          {legacyQuery.data.items.map(work => <LegacyWorkCard
            key={`${work.work_type}:${work.work_id}`}
            work={work}
            eligibleUsers={query.data.users.filter(user => user.role === 'hrbp' || user.role === 'hr_manager')}
            claiming={claimWork.isPending && claimWork.variables?.work.work_id === work.work_id}
            onClaim={userId => claimWork.mutate({ work, userId })}
          />)}
        </div>}
      </section>
    </>}
  </main>
}

function LegacyWorkCard({
  work,
  eligibleUsers,
  claiming,
  onClaim,
}: {
  work: LegacyWorkView
  eligibleUsers: AdminUserView[]
  claiming: boolean
  onClaim: (userId: string) => void
}) {
  const [userId, setUserId] = useState('')
  const typeLabel =
    work.work_type === 'weekly_report'
      ? '历史周报'
      : work.work_type === 'knowledge_feedback_candidate'
        ? '待归属知识反馈候选'
        : work.work_type === 'culture_content'
          ? '无归属文化草稿'
          : '历史异步分析'
  return <article aria-label={work.title}>
    <strong>{work.title}</strong>
    <p>{typeLabel}</p>
    <label>{work.title} 的负责人
      <select value={userId} onChange={event => setUserId(event.target.value)} disabled={claiming}>
        <option value="">请选择已核实的负责人</option>
        {eligibleUsers.map(user => <option key={user.user_id} value={user.user_id}>{user.name}（{ROLE_LABELS[user.role]}）</option>)}
      </select>
    </label>
    <button type="button" disabled={!userId || claiming} onClick={() => onClaim(userId)}>
      {claiming ? '正在认领…' : `认领 ${work.title}`}
    </button>
  </article>
}

function UserAccessCard({
  user,
  orgUnits,
  assigning,
  savingScopes,
  onAssign,
  onSaveScopes,
}: {
  user: AdminUserView
  orgUnits: OrgUnitView[]
  assigning: boolean
  savingScopes: boolean
  onAssign: (orgUnitId: string | null) => void
  onSaveScopes: (orgUnitIds: string[]) => void
}) {
  const [scopeIds, setScopeIds] = useState<string[]>(user.manager_scope_org_unit_ids)
  const scopeNames = user.manager_scope_org_unit_ids
    .map(id => orgUnits.find(org => org.org_unit_id === id)?.name)
    .filter((name): name is string => Boolean(name))

  function toggleScope(orgUnitId: string) {
    setScopeIds(current => current.includes(orgUnitId) ? current.filter(id => id !== orgUnitId) : [...current, orgUnitId])
  }

  return <article aria-label={user.name}>
    <strong>{user.name}</strong>
    <p>{user.email}</p>
    <small>{ROLE_LABELS[user.role]} · {user.org_unit ?? '未分配组织'}</small>
    <label>{user.name} 的组织
      <select value={user.org_unit_id ?? ''} disabled={assigning} onChange={event => onAssign(event.target.value || null)}>
        <option value="">未分配组织</option>
        {orgUnits.map(org => <option key={org.org_unit_id} value={org.org_unit_id}>{org.name}</option>)}
      </select>
    </label>
    {user.role === 'hr_manager' && <fieldset>
      <legend>经理可见组织范围</legend>
      {orgUnits.length === 0 && <p>请先创建组织单元。</p>}
      {orgUnits.map(org => <label key={org.org_unit_id}>
        <input type="checkbox" checked={scopeIds.includes(org.org_unit_id)} onChange={() => toggleScope(org.org_unit_id)} />
        <span className="sr-only">{user.name} 授权 </span>{org.name}
      </label>)}
      <button type="button" disabled={savingScopes} onClick={() => onSaveScopes(scopeIds)}>
        {savingScopes ? '正在保存…' : `保存 ${user.name} 的经理范围`}
      </button>
      <p>{scopeNames.length > 0 ? `经理范围：${scopeNames.join('、')}` : '经理范围：仅自己（尚未授权组织）'}</p>
    </fieldset>}
  </article>
}
