/** HRBP AI Workbench — Upload zone component for InterviewDigest. */

import { useRef } from 'react';
import clsx from 'clsx';

interface UploadZoneProps {
  onUpload: (files: FileList) => void;
  disabled?: boolean;
}

export function UploadZone({ onUpload, disabled }: UploadZoneProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div
      onClick={() => !disabled && inputRef.current?.click()}
      className={clsx(
        'border-2 border-dashed rounded-xl p-8 text-center transition-all duration-normal',
        disabled
          ? 'border-neutral-200 bg-neutral-50 cursor-not-allowed opacity-50'
          : 'border-neutral-300 cursor-pointer hover:border-accent-400 hover:bg-accent-50'
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".docx,.pdf,.txt"
        multiple
        onChange={(e) => e.target.files && onUpload(e.target.files)}
        className="hidden"
        disabled={disabled}
      />
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
           className="mx-auto mb-3 text-neutral-400" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="17 8 12 3 7 8" />
        <line x1="12" y1="3" x2="12" y2="15" />
      </svg>
      <div className="text-body text-neutral-500">点击或拖拽上传访谈文件</div>
      <div className="text-caption text-neutral-400 mt-1">支持 docx / pdf / txt 格式</div>
    </div>
  );
}
