import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { DocumentUploader } from '../../src/features/knowledge-base/KnowledgeBasePage'

test('rejects unsupported document types before upload', async () => {
  render(<QueryClientProvider client={new QueryClient()}><DocumentUploader kbId="kb-1" /></QueryClientProvider>)
  fireEvent.change(screen.getByLabelText('上传制度文件'), { target: { files: [new File(['x'], 'policy.xls', { type: 'application/vnd.ms-excel' })] } })
  expect(screen.getByRole('alert')).toHaveTextContent('仅支持 TXT、PDF、DOCX')
})
