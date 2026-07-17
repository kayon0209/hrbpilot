import { useState, useEffect } from 'react';
import { api } from '../../lib/api';
import { toast } from '@/components/ui/Toast';
import { useAuth } from '@/hooks/useAuth';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

interface KBItem { id: string; name: string; docs: number; status: string; scenario_id?: string; }

export function KBManagementPage() {
  const [showCreate, setShowCreate] = useState(false);
  const [kbItems, setKbItems] = useState<KBItem[]>([]);
  const [uploadingFor, setUploadingFor] = useState<string | null>(null);
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; name: string } | null>(null);
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';

  const fetchKBs = () => {
    api.get<{ knowledge_bases?: KBItem[] }>('/api/kb/list')
      .then((d) => setKbItems(d.knowledge_bases || []))
      .catch(() => setKbItems([]));
  };

  useEffect(() => { fetchKBs(); }, []);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    try {
      await api.post('/api/kb/create', { name: newName, description: newDesc, scenario_id: 'policy_kb', chunk_strategy: 'fixed_512' });
      setShowCreate(false); setNewName(''); setNewDesc('');
      toast.success('知识库创建成功');
      fetchKBs();
    } catch { toast.error('创建失败'); }
  };

  const handleUpload = async (kbId: string, files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploadingFor(kbId);
    try {
      for (const file of Array.from(files)) {
        const formData = new FormData();
        formData.append('file', file);
        await api.post('/api/kb/' + kbId + '/upload', formData);
      }
      toast.success('上传成功，点击「触发索引」加入检索');
      fetchKBs();
    } catch { toast.error('上传失败'); }
    finally { setUploadingFor(null); }
  };

  const handleIngest = async (kbId: string) => {
    try {
      await api.post('/api/kb/' + kbId + '/ingest', {});
      toast.success('索引已触发');
      fetchKBs();
    } catch { toast.error('索引失败'); }
  };

  const handleDeleteClick = (kbId: string, name: string) => {
    setDeleteTarget({ id: kbId, name });
  };

  const handleDeleteConfirm = async () => {
    if (!deleteTarget) return;
    try {
      const token = localStorage.getItem('hrbp-auth');
      const parsed = token ? JSON.parse(token) : null;
      const accessToken = parsed?.state?.accessToken || '';
      const res = await fetch('/api/kb/delete', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + accessToken
        },
        body: JSON.stringify({ kb_id: deleteTarget.id })
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as Record<string,string>).message || 'HTTP ' + res.status);
      }
      toast.success('已删除「' + deleteTarget.name + '」');
      setDeleteTarget(null);
      fetchKBs();
    } catch (err) { 
      const msg = err instanceof Error ? err.message : '删除失败';
      toast.error(msg.includes('403') || msg.includes('Forbidden') ? '权限不足，需要管理员权限' : '删除失败: ' + msg);
    }
  };

  const STATUS_MAP: Record<string, { bg: string; text: string; border: string; label: string }> = {
    active: { bg: 'bg-emerald-50', text: 'text-emerald-600', border: 'border-emerald-200', label: '已激活' },
    building: { bg: 'bg-warning-50', text: 'text-warning-600', border: 'border-warning-200', label: '索引中' },
    error: { bg: 'bg-danger-50', text: 'text-danger-600', border: 'border-danger-200', label: '异常' },
  };

  return (
    <div className="flex-1 overflow-auto px-8 py-6 bg-neutral-50">
      <h1 className="text-page-title text-neutral-800 mb-1">知识库管理</h1>
      <p className="text-body text-neutral-400 mb-6">创建和管理 RAG 知识库，上传文档并触发索引</p>

      <button onClick={() => setShowCreate(true)} className="btn-primary mb-6">新建知识库</button>

      {kbItems.length === 0 && (
        <div className="card text-center text-caption text-neutral-400 py-16">
          暂无知识库，点击「新建知识库」开始
        </div>
      )}

      <div className="space-y-3">
        {kbItems.map((kb) => {
          const status = STATUS_MAP[kb.status] || STATUS_MAP.active;
          return (
            <div key={kb.id} className="card">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-4">
                  <div className="w-10 h-10 rounded-lg bg-accent-50 flex items-center justify-center shrink-0">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
                         className="text-accent-500" strokeLinecap="round" strokeLinejoin="round">
                      <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
                    </svg>
                  </div>
                  <div>
                    <div className="text-card-title text-neutral-700 font-medium">{kb.name}</div>
                    <div className="text-caption text-neutral-400 mt-0.5">
                      0 documents · <span className={'badge border ' + status.border + ' ' + status.bg + ' ' + status.text}>{status.label}</span>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <label className={'btn-secondary text-caption px-3 py-1.5 h-auto cursor-pointer ' + (uploadingFor === kb.id ? 'opacity-50' : '')}>
                    {uploadingFor === kb.id ? '上传中...' : '上传文档'}
                    <input type="file" accept=".pdf,.docx,.txt,.doc" multiple className="hidden"
                           onChange={(e) => { handleUpload(kb.id, e.target.files); e.target.value = ''; }}
                           disabled={uploadingFor === kb.id} />
                  </label>
                  <button onClick={() => handleIngest(kb.id)} className="btn-secondary text-caption px-3 py-1.5 h-auto">触发索 引</button>
                  {isAdmin && (
                    <button onClick={() => handleDeleteClick(kb.id, kb.name)} className="text-caption text-neutral-400 hover:text-danger-500 px-2 py-1.5 transition-colors ml-1">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      </svg>
                    </button>
                  )}
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {showCreate && (
        <div className="fixed inset-0 bg-neutral-900/30 flex items-center justify-center z-50" onClick={() => setShowCreate(false)}>
          <div className="card max-w-md w-full" onClick={(e) => e.stopPropagation()}>
            <h2 className="text-section-title text-neutral-800 mb-4">新建知识库</h2>
            <div className="space-y-4">
              <div>
                <label className="text-body-sm text-neutral-500 block mb-1.5">知识库名称</label>
                <input value={newName} onChange={(e) => setNewName(e.target.value)} className="input-base w-full" placeholder="如：公司制度库" />
              </div>
              <div>
                <label className="text-body-sm text-neutral-500 block mb-1.5">描述</label>
                <textarea value={newDesc} onChange={(e) => setNewDesc(e.target.value)} className="input-base w-full h-20 resize-none" placeholder="知识库用途说明" />
              </div>
              <div className="flex gap-3">
                <button onClick={() => setShowCreate(false)} className="btn-secondary">取消</button>
                <button onClick={handleCreate} className="btn-primary">创建</button>
              </div>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除知识库"
        message={'确认删除「' + (deleteTarget?.name || '') + '」及其中所有文档？此操作不可恢复。'}
        confirmLabel="确认删除"
        danger
        onConfirm={handleDeleteConfirm}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
