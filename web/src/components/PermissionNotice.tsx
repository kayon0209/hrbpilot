export function PermissionNotice({ feature, requiredRole = 'HRBP' }: { feature: string; requiredRole?: string }) {
  return <section className="permission-notice"><span aria-hidden="true">⌁</span><div><h2>需要 {requiredRole} 权限</h2><p>当前账号不能使用{feature}。如确有工作需要，请联系管理员调整角色。</p></div></section>
}
