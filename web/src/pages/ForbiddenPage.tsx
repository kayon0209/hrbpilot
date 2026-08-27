import { Link, useLocation } from 'react-router-dom'

export function ForbiddenPage() {
  const location = useLocation()
  const state = location.state as { requiredRoles?: string[]; from?: string } | null
  return (
    <main className="center-state">
      <h1>暂无权限</h1>
      <p>当前角色不能访问此工作区。</p>
      {state?.requiredRoles && (
        <p>
          该功能需要角色：{state.requiredRoles.join('、')}。如确有工作需要，请联系管理员调整角色。
        </p>
      )}
      <p>
        <Link className="secondary-button" to="/">
          返回概览
        </Link>
      </p>
    </main>
  )
}
