export function PermissionNotice({ feature, requiredRole = 'HRBP' }: { feature: string; requiredRole?: string }) {
  return <section className="permission-notice"><span aria-hidden="true"><svg className="permission-notice__lock" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6"><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V8a4 4 0 1 1 8 0v3" /></svg></span><div><h2>需要 {requiredRole} 权限</h2><p>当前账号不能使用{feature}。如确有工作需要，请联系管理员调整角色。</p></div></section>
}
