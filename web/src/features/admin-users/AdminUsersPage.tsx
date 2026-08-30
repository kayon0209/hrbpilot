import { useQuery } from '@tanstack/react-query'
import { listAdminUsers } from '../../api/admin-users'
import { AsyncState } from '../../components/AsyncState'

const ROLE_LABELS = { employee: '员工', hrbp: 'HRBP', hr_manager: 'HR 经理', admin: '管理员' } as const

export function AdminUsersPage() {
  const query = useQuery({ queryKey: ['admin-users'], queryFn: listAdminUsers })
  return <main className="page-stack">
    <header className="page-heading"><div><span className="eyebrow">管理后台</span><h1>用户与权限</h1><p>核对每位用户的角色与组织归属。业务材料是否可见还取决于明确的对象和组织授权。</p></div></header>
    {query.isPending && <AsyncState kind="loading" title="正在读取用户" />}
    {query.isError && <AsyncState kind="error" title="用户读取失败" detail={query.error.message} action={<button onClick={() => query.refetch()}>重试</button>} />}
    {query.data && <section className="panel"><div className="issue-list">{query.data.users.map(user => <article key={user.user_id}>
      <strong>{user.name}</strong>
      <p>{user.email}</p>
      <small>{ROLE_LABELS[user.role]} · {user.org_unit ?? '未分配组织'}</small>
    </article>)}</div></section>}
  </main>
}
