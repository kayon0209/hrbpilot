import { useEffect, useRef } from 'react'
import { Link, useLocation } from 'react-router-dom'

export function ForbiddenPage() {
  const location = useLocation()
  const heading = useRef<HTMLHeadingElement>(null)
  useEffect(() => { heading.current?.focus() }, [])
  const state = location.state as { requiredRoles?: string[]; from?: string } | null
  return (
    <main className="center-state">
      <h1 ref={heading} tabIndex={-1}>暂无权限</h1>
      <p>当前角色不能访问此工作区。</p>
      {state?.requiredRoles && (
        <p>
          该功能需要角色：{state.requiredRoles.join('、')}。如确有工作需要，请联系管理员调整角色。
        </p>
      )}
      <p>
        <Link className="secondary-button" to="/">
          回到我的工作台
        </Link>
      </p>
    </main>
  )
}
