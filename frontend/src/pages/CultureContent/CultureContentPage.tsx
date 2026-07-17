/** HRBP AI Workbench — CultureContent (Redesigned) */

import { useState } from 'react';
import { api } from '../../lib/api';

interface CultureContent { news_article: string; group_notice: string; employee_story: string; event_copy: string; keywords_used: string[]; tone: string; confidence: number; }
interface KeywordExpansion { original: string[]; expanded: string[]; categories: Record<string, string[]>; }

const CHANNEL_TABS = [
  { key: 'news_article', label: '新闻稿', desc: '800-1200字 · 正式庄重' },
  { key: 'group_notice', label: '群通知', desc: '100-200字 · 简洁有力' },
  { key: 'employee_story', label: '员工故事', desc: '500-800字 · 温情叙事' },
  { key: 'event_copy', label: '活动文案', desc: '200-400字 · 吸引号召' },
];

export function CultureContentPage() {
  const [keywords, setKeywords] = useState('');
  const [expandedKeywords, setExpandedKeywords] = useState<KeywordExpansion | null>(null);
  const [result, setResult] = useState<CultureContent | null>(null);
  const [activeTab, setActiveTab] = useState('news_article');
  const [isGenerating, setIsGenerating] = useState(false);
  const [isExpanding, setIsExpanding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleExpand = async () => {
    if (!keywords.trim()) return;
    setIsExpanding(true);
    try {
      const response = await api.post<KeywordExpansion>('/api/culture-content/expand-keywords', { keywords: keywords.split(/[,，\s]+/).filter(Boolean), expand_keywords: true });
      setExpandedKeywords(response);
    } catch (err) { setError(`扩展失败: ${(err as Error).message}`); } finally { setIsExpanding(false); }
  };

  const handleGenerate = async () => {
    setIsGenerating(true); setError(null);
    try {
      const kwList = keywords.split(/[,，\s]+/).filter(Boolean);
      const response = await api.post<{ content_id: string; content: CultureContent }>('/api/culture-content/generate', { keywords: kwList, tone: '积极向上', expand_keywords: true });
      setResult(response.content);
    } catch (err) { setError(`生成失败: ${(err as Error).message}`); } finally { setIsGenerating(false); }
  };

  const currentContent = result ? (result[activeTab as keyof CultureContent] as string) : '';

  return (
    <div className="flex-1 overflow-auto px-8 py-6 bg-neutral-50">
      <h1 className="text-page-title text-neutral-800 mb-1">文化传播</h1>
      <p className="text-body text-neutral-400 mb-6">输入关键词，AI 自动生成4个渠道的文化传播内容</p>

      <div className="card mb-6">
        <div className="flex items-center gap-3 mb-4">
          <input value={keywords} onChange={(e) => setKeywords(e.target.value)} className="input-base flex-1" placeholder="输入关键词，如: 团队协作, 创新, 关怀" />
          <button onClick={handleExpand} disabled={isExpanding} className="btn-secondary">{isExpanding ? '扩展中...' : '扩展关键词'}</button>
          <button onClick={handleGenerate} disabled={isGenerating || !keywords.trim()} className="btn-primary">{isGenerating ? '生成中...' : '生成内容'}</button>
        </div>

        {expandedKeywords && (
          <div className="mt-3">
            <div className="text-caption text-neutral-500 mb-2">扩展关键词</div>
            <div className="flex gap-2 flex-wrap">
              {expandedKeywords.expanded.map((kw) => (
                <span key={kw} className="badge bg-rose-50 text-rose-600 border-rose-200">{kw}</span>
              ))}
            </div>
          </div>
        )}
      </div>

      {!result && !isGenerating && !expandedKeywords && (
        <div className="flex items-center justify-center py-16">
          <div className="text-center">
            <div className="w-14 h-14 mx-auto rounded-2xl bg-rose-50 flex items-center justify-center mb-4">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                   strokeWidth="1.8" className="text-rose-500" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19l7-7 3 3-7 7-3-3z" /><path d="M18 13l-1.5-7.5L2 2l3.5 14.5L13 18l5-5z" />
                <path d="M2 2l7.586 7.586" /><circle cx="11" cy="11" r="2" />
              </svg>
            </div>
            <div className="text-section-title text-neutral-700 mb-2">输入关键词开始创作</div>
            <div className="text-body text-neutral-400 max-w-sm">AI 将自动扩展关键词，并在4个渠道（新闻稿、群通知、员工故事、活动文案）生成适配内容</div>
          </div>
        </div>
      )}

      {error && <div className="px-4 py-3 rounded-md bg-danger-50 border border-danger-200 text-body-sm text-danger-600 mb-4">{error}</div>}
      {isGenerating && <div className="animate-pulse text-primary-500 text-caption mb-4">正在为4个渠道生成内容...</div>}

      {result && (
        <div className="card overflow-hidden">
          <div className="flex border-b border-neutral-200">
            {CHANNEL_TABS.map((tab) => (
              <button key={tab.key} onClick={() => setActiveTab(tab.key)}
                className={`flex-1 px-5 py-3 text-center transition-all duration-fast ${
                  activeTab === tab.key
                    ? 'bg-rose-50 border-b-2 border-rose-500 text-rose-600 font-medium'
                    : 'text-neutral-500 hover:bg-neutral-50'
                }`}>
                <span className="text-card-title">{tab.label}</span>
                <span className="text-caption text-neutral-400 ml-1">{tab.desc}</span>
              </button>
            ))}
          </div>

          <div className="px-6 py-5">
            <div className="text-body text-neutral-700 whitespace-pre-wrap leading-relaxed">{currentContent}</div>
            <div className="flex items-center justify-between mt-4">
              <div className="text-caption text-neutral-400">字数: {currentContent.length} · 基调: {result.tone} · 置信度: {(result.confidence * 100).toFixed(0)}%</div>
            </div>
          </div>

          <div className="px-6 py-3 border-t border-neutral-100">
            <div className="flex gap-2 flex-wrap">
              {result.keywords_used.map((kw) => <span key={kw} className="badge bg-rose-50 text-rose-600 border-rose-200">{kw}</span>)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
