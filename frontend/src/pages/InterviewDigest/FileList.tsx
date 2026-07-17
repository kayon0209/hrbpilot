/** HRBP AI Workbench — File list component for InterviewDigest. */

interface UploadedDoc { filename: string; content_type: string; text_length: number; }

interface FileListProps {
  docs: UploadedDoc[];
  onRemove: (index: number) => void;
}

export function FileList({ docs, onRemove }: FileListProps) {
  if (docs.length === 0) return null;

  return (
    <div className="space-y-2">
      {docs.map((doc, i) => (
        <div key={i} className="card flex items-center gap-3">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
               className="text-accent-500 shrink-0" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
          </svg>
          <div className="flex-1 min-w-0">
            <div className="text-body-sm text-neutral-700 font-medium truncate">{doc.filename}</div>
            <div className="text-caption text-neutral-400">{doc.text_length} chars · {doc.content_type}</div>
          </div>
          <button
            onClick={() => onRemove(i)}
            className="text-caption text-neutral-400 hover:text-danger-500 transition-colors shrink-0"
          >
            删除
          </button>
        </div>
      ))}
    </div>
  );
}
