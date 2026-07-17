import { useState, useRef, useCallback, useEffect } from 'react';
import { authStore } from '@/stores/authStore';
import { useAuth } from '@/hooks/useAuth';
import { toast } from '@/components/ui/Toast';

interface Citation {
  document_name: string;
  section: string;
  content_snippet: string;
  confidence: number;
}

interface Message {
  role: 'user' | 'assistant';
  content: string;
  citations?: Citation[];
  confidence?: number;
  hasEvidence?: boolean;
  isLoading?: boolean;
  feedback?: 'up' | 'down' | null;
  feedbackCounts?: { up: number; down: number };
}

interface SSEData { event: string; data: string; }

const CATEGORIES = [
  { id: 'all', label: '\u5168\u90e8\u5236\u5ea6' },
  { id: '\u4f11\u5047', label: '\u4f11\u5047' },
  { id: '\u85aa\u916c', label: '\u85aa\u916c' },
  { id: '\u7ee9\u6548', label: '\u7ee9\u6548' },
  { id: '\u8003\u52e4', label: '\u8003\u52e4' },
  { id: '\u57f9\u8bad', label: '\u57f9\u8bad' },
  { id: '\u52b3\u52a8\u5408\u540c', label: '\u52b3\u52a8\u5408\u540c' },
];

export function PolicyQAPage() {
  const [question, setQuestion] = useState('');
  const [messages, setMessages] = useState<Message[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [activeTab, setActiveTab] = useState('all');
  const [history, setHistory] = useState<Array<{ id: string; question: string; date: string }>>([]);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  useAuth();

  const scrollToBottom = useCallback(() => {
    setTimeout(() => messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' }), 50);
  }, []);

  useEffect(() => { scrollToBottom(); }, [messages]);

  const handleSubmit = async () => {
    if (!question.trim() || isStreaming) return;
    const categoryLabel = CATEGORIES.find(c => c.id === activeTab)?.label || '\u5168\u90e8\u5236\u5ea6';

    const userMsg: Message = { role: 'user', content: question };
    setMessages((prev) => [...prev, userMsg]);

    const assistantMsg: Message = {
      role: 'assistant', content: '', citations: [], confidence: 0, hasEvidence: true, isLoading: true,
      feedback: null, feedbackCounts: { up: 0, down: 0 },
    };
    setMessages((prev) => [...prev, assistantMsg]);
    setIsStreaming(true);
    const currentQuestion = question;
    setQuestion('');

    try {
      const token = authStore.getState().accessToken || '';
      const baseUrl = (import.meta as Record<string, unknown>).env?.VITE_API_URL as string || '';
      const response = await fetch(baseUrl + '/api/policy-qa/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token },
        body: JSON.stringify({ question: currentQuestion, stream: true, category: activeTab }),
      });

      if (!response.ok) throw new Error('API error: ' + response.status);

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let accumulatedText = '';
      let citations: Citation[] = [];
      let confidence = 0;
      let hasEvidence = true;

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value, { stream: true });
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
              const sseData: SSEData = JSON.parse(line.slice(6));
              const payload = JSON.parse(sseData.data);
              if (sseData.event === 'chunk') {
                accumulatedText += payload.text;
                setMessages((prev) => { const last = prev[prev.length - 1]; return [...prev.slice(0, -1), { ...last, content: accumulatedText, isLoading: true }]; });
                scrollToBottom();
              } else if (sseData.event === 'sources') {
                citations = payload;
                setMessages((prev) => { const last = prev[prev.length - 1]; return [...prev.slice(0, -1), { ...last, citations }]; });
              } else if (sseData.event === 'correction') {
                accumulatedText = payload.full_text;
                setMessages((prev) => { const last = prev[prev.length - 1]; return [...prev.slice(0, -1), { ...last, content: accumulatedText }]; });
              } else if (sseData.event === 'done') {
                confidence = payload.confidence;
                hasEvidence = payload.has_evidence;
                setMessages((prev) => { const last = prev[prev.length - 1]; return [...prev.slice(0, -1), { ...last, isLoading: false, confidence, hasEvidence }]; });
              } else if (sseData.event === 'error') {
                setMessages((prev) => { const last = prev[prev.length - 1]; return [...prev.slice(0, -1), { ...last, content: payload.message, isLoading: false }]; });
              }
            } catch { /* ignore malformed SSE */ }
          }
        }
      }
      setHistory((prev) => [{ id: Date.now().toString(), question: currentQuestion, date: new Date().toLocaleTimeString() }, ...prev.slice(0, 19)]);
    } catch (error) {
      setMessages((prev) => { const last = prev[prev.length - 1]; return [...prev.slice(0, -1), { ...last, content: '\u8bf7\u6c42\u5931\u8d25: ' + (error as Error).message, isLoading: false }]; });
    } finally {
      setIsStreaming(false);
    }
  };

  const handleFeedback = (messageIndex: number, rating: 'up' | 'down') => {
    setMessages((prev) => prev.map((msg, i) => {
      if (i !== messageIndex) return msg;
      const oldCounts = msg.feedbackCounts || { up: 0, down: 0 };
      const oldFeedback = msg.feedback;
      // Toggle off
      if (oldFeedback === rating) {
        return { ...msg, feedback: null, feedbackCounts: { ...oldCounts, [rating]: Math.max(0, oldCounts[rating] - 1) } };
      }
      // New vote or switch
      const newCounts = { ...oldCounts };
      if (oldFeedback && oldFeedback !== rating) {
        newCounts[oldFeedback] = Math.max(0, newCounts[oldFeedback] - 1);
      }
      newCounts[rating] = (newCounts[rating] || 0) + 1;
      return { ...msg, feedback: rating, feedbackCounts: newCounts };
    }));
  };

  return (
    <div className="flex h-[calc(100vh-56px)]">
      <div className="flex-1 flex flex-col min-w-0 bg-white">
        {/* Category tabs */}
        <div className="flex gap-1 px-4 py-2 border-b border-neutral-200 bg-neutral-50 overflow-x-auto">
          {CATEGORIES.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={'flex-shrink-0 px-3 py-1.5 rounded text-xs font-medium transition-all duration-fast ' + (
                activeTab === tab.id
                  ? 'bg-indigo-500 text-white shadow-sm'
                  : 'text-neutral-500 hover:bg-indigo-50 hover:text-indigo-600 border border-transparent hover:border-indigo-200'
              )}>
              {tab.label}
            </button>
          ))}
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-auto px-5 py-4">
          {messages.length === 0 && (
            <div className="flex items-center justify-center" style={{ minHeight: 'calc(100vh - 340px)' }}>
              <div className="text-center max-w-md">
                <div className="w-12 h-12 mx-auto rounded-xl bg-indigo-50 flex items-center justify-center mb-3">
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                       strokeWidth="1.8" className="text-indigo-500" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" /><path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
                  </svg>
                </div>
                <div className="text-lg text-neutral-700 font-medium mb-2">HR 制度问答</div>
                <div className="text-sm text-neutral-400 mb-4">
                  输入制度问题，AI 从知识库检索并给出带引用的回答
                </div>
                <div className="flex gap-2 justify-center flex-wrap">
                  {['\u5e74\u5047\u600e\u4e48\u4f11\uff1f', '\u52a0\u73ed\u8d39\u600e\u4e48\u7b97\uff1f', '\u8bd5\u7528\u671f\u591a\u4e45\uff1f'].map((q) => (
                    <button key={q} onClick={() => setQuestion(q)}
                      className="text-xs px-3 py-1.5 rounded-md border border-neutral-200 text-neutral-500 hover:bg-indigo-50 hover:border-indigo-200 hover:text-indigo-600 transition-all">
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="space-y-4 max-w-3xl mx-auto">
            {messages.map((msg, i) => (
              <div key={i} className={'flex ' + (msg.role === 'user' ? 'justify-end' : 'justify-start')}>

                {/* User message */}
                {msg.role === 'user' && (
                  <div className="bg-indigo-500 text-white rounded-2xl rounded-br-md px-4 py-2.5 text-sm max-w-[75%]">
                    {msg.content}
                  </div>
                )}

                {/* Assistant message */}
                {msg.role === 'assistant' && (
                  <div className="max-w-[85%] w-full">
                    {msg.isLoading ? (
                      <div className="flex items-center gap-2 text-sm text-neutral-400 py-2">
                        <span className="animate-pulse text-indigo-400">{'\u25cf'}</span> 正在检索制度库并生成回答...
                      </div>
                    ) : (
                      <>
                        {/* Content */}
                        <div className="text-sm text-neutral-700 leading-relaxed whitespace-pre-wrap">
                          {msg.content}
                        </div>

                        {/* Confidence warning */}
                        {msg.hasEvidence === false && msg.content && (
                          <div className="mt-2 px-3 py-2 rounded-md bg-warning-50 border border-warning-200 text-xs text-warning-600">
                            未在现有制度中找到相关依据，建议联系 HR 部门确认
                          </div>
                        )}

                        {/* Citations — compact, small */}
                        {msg.citations && msg.citations.length > 0 && (
                          <div className="mt-3 space-y-1">
                            <div className="text-[11px] text-neutral-400 font-medium uppercase tracking-wide mb-1">引用来源</div>
                            {msg.citations.map((c, ci) => (
                              <div key={ci} className="flex items-start gap-2 px-2.5 py-1.5 rounded bg-neutral-50 border border-neutral-100 text-[11px]">
                                <span className="text-indigo-500 font-medium shrink-0">[{c.document_name} {c.section}]</span>
                                <span className="text-neutral-400 line-clamp-1">{c.content_snippet}</span>
                              </div>
                            ))}
                          </div>
                        )}

                        {/* Feedback buttons */}
                        {msg.content && !msg.isLoading && (
                          <div className="flex items-center gap-3 mt-3 pt-2 border-t border-neutral-100">
                            <button
                              onClick={() => handleFeedback(i, 'up')}
                              className={'flex items-center gap-1.5 text-xs px-2.5 py-1 rounded transition-colors ' + (
                                msg.feedback === 'up'
                                  ? 'bg-emerald-50 text-emerald-600'
                                  : 'text-neutral-400 hover:text-emerald-600 hover:bg-emerald-50'
                              )}>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill={msg.feedback === 'up' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z" />
                                <path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
                              </svg>
                              <span>{msg.feedbackCounts?.up || 0}</span>
                            </button>
                            <button
                              onClick={() => handleFeedback(i, 'down')}
                              className={'flex items-center gap-1.5 text-xs px-2.5 py-1 rounded transition-colors ' + (
                                msg.feedback === 'down'
                                  ? 'bg-danger-50 text-danger-600'
                                  : 'text-neutral-400 hover:text-danger-600 hover:bg-danger-50'
                              )}>
                              <svg width="14" height="14" viewBox="0 0 24 24" fill={msg.feedback === 'down' ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z" />
                                <path d="M17 2h3a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-3" />
                              </svg>
                              <span>{msg.feedbackCounts?.down || 0}</span>
                            </button>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
            <div ref={messagesEndRef} />
          </div>
        </div>

        {/* Input bar — fixed height, bottom */}
        <div className="border-t border-neutral-200 bg-white px-5 py-3">
          <div className="flex gap-2 max-w-3xl mx-auto">
            <input
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSubmit()}
              className="flex-1 h-10 px-3 rounded-lg border border-neutral-300 text-sm focus:border-indigo-400 focus:ring-1 focus:ring-indigo-400 outline-none bg-neutral-50 placeholder:text-neutral-400"
              placeholder={activeTab === 'all' ? '\u8f93\u5165\u60a8\u7684\u5236\u5ea6\u95ee\u9898...' : '\u5728\u300c' + (CATEGORIES.find(c => c.id === activeTab)?.label || '') + '\u300d\u5236\u5ea6\u8303\u56f4\u5185\u63d0\u95ee...'}
              disabled={isStreaming}
            />
            <button
              onClick={handleSubmit}
              disabled={isStreaming || !question.trim()}
              className="h-10 px-5 rounded-lg bg-indigo-500 hover:bg-indigo-600 disabled:opacity-40 text-white text-sm font-medium transition-colors">
              {isStreaming ? '\u751f\u6210\u4e2d...' : '\u63d0\u95ee'}
            </button>
          </div>
        </div>
      </div>

      {/* History panel */}
      <div className="w-64 shrink-0 border-l border-neutral-200 bg-white p-4 flex flex-col">
        <h3 className="text-sm text-neutral-500 font-medium mb-3">历史问答</h3>
        {history.length === 0 ? (
          <div className="text-xs text-neutral-300 mt-8 text-center">暂无记录</div>
        ) : (
          <div className="space-y-2 overflow-auto flex-1">
            {history.map((h) => (
              <button key={h.id} onClick={() => setQuestion(h.question)}
                className="w-full text-left px-3 py-2 rounded-md bg-neutral-50 border border-neutral-100 hover:border-indigo-200 text-xs text-neutral-600 truncate transition-colors">
                <div>{h.question}</div>
                <div className="text-[10px] text-neutral-400 mt-0.5">{h.date}</div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
