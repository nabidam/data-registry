import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import { Card, PageHeader, Select } from '@/components/ui'
import { DataTable } from '@/components/DataTable'
import { api } from '@/lib/api'
import type { Row } from '@/types'

type SnapshotExport = {
  snapshot_id: number
  name: string
  prefix_uri: string | null
  counts: Record<string, number>
  created_at: string
}

type ExportFormat = 'parquet' | 'csv' | 'tsv' | 'jsonl' | 'huggingface'

const columns = [
  { id: 'source_text', label: 'Source text', group: 'Translation' },
  { id: 'target_text', label: 'Target text', group: 'Translation' },
  { id: 'src_lang', label: 'Source language', group: 'Language' },
  { id: 'tgt_lang', label: 'Target language', group: 'Language' },
  { id: 'sample_id', label: 'Sample ID', group: 'Provenance' },
  { id: 'batch_id', label: 'Batch ID', group: 'Provenance' },
  { id: 'source_id', label: 'Source ID', group: 'Provenance' },
  { id: 'domain', label: 'Domain', group: 'Metadata' },
  { id: 'quality', label: 'Quality', group: 'Metadata' },
  { id: 'document_id', label: 'Document ID', group: 'Metadata' },
  { id: 'meta', label: 'Imported metadata', group: 'Metadata' },
] as const

const allColumns = columns.map((column) => column.id)
const translationColumns = ['source_text', 'target_text', 'src_lang', 'tgt_lang']
const splitOptions = ['train', 'validation', 'test'] as const

const formatOptions: Array<{ value: ExportFormat; label: string; detail: string }> = [
  { value: 'parquet', label: 'Parquet', detail: 'Columnar, compressed' },
  { value: 'jsonl', label: 'JSONL', detail: 'One record per line' },
  { value: 'csv', label: 'CSV', detail: 'Spreadsheet-friendly' },
  { value: 'tsv', label: 'TSV', detail: 'Tab-separated text' },
  { value: 'huggingface', label: 'Hugging Face dataset', detail: 'Loadable DatasetDict package' },
]

export default function Exports() {
  const { data, isLoading } = useQuery({
    queryKey: ['exports'],
    queryFn: () => api.get<SnapshotExport[]>('/exports'),
  })
  const [snapshotId, setSnapshotId] = useState('')
  const [format, setFormat] = useState<ExportFormat>('parquet')
  const [selectedColumns, setSelectedColumns] = useState<string[]>(translationColumns)
  const [selectedSplits, setSelectedSplits] = useState<string[]>([...splitOptions])
  const [includeManifest, setIncludeManifest] = useState(true)

  const activeSnapshotId = snapshotId || (data?.[0] ? String(data[0].snapshot_id) : '')
  const groupedColumns = useMemo(
    () => [...new Set(columns.map((column) => column.group))].map((group) => ({
      group,
      columns: columns.filter((column) => column.group === group),
    })),
    [],
  )
  const isHuggingFace = format === 'huggingface'
  const effectiveSplits = isHuggingFace ? [...splitOptions] : selectedSplits
  const customUrl = activeSnapshotId && selectedColumns.length && effectiveSplits.length
    ? api.downloadUrl(
        `/exports/${activeSnapshotId}/custom?${new URLSearchParams({
          format,
          columns: selectedColumns.join(','),
          splits: effectiveSplits.join(','),
          include_manifest: String(isHuggingFace || includeManifest),
        }).toString()}`,
      )
    : undefined

  const toggle = (value: string, selected: string[], setSelected: (values: string[]) => void) => {
    setSelected(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value])
  }
  const directLink = (id: number, file: string) => (
    <a
      key={file}
      className="mr-3 text-sm text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-slate-950"
      href={api.downloadUrl(`/exports/${id}/${file}`)}
    >
      {file}
    </a>
  )

  return (
    <>
      <PageHeader
        title="Exports"
        subtitle="Download immutable snapshots as-is, or prepare a purpose-built research package."
      />

      <Card className="mb-6 overflow-hidden p-0">
        <div className="border-b border-slate-200 bg-slate-50 px-5 py-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="font-semibold text-slate-900">Custom snapshot export</h2>
              <p className="mt-1 text-sm text-slate-500">
                Your choices create a download only; the source snapshot remains unchanged.
              </p>
            </div>
            <span className="rounded-full bg-slate-200 px-2.5 py-1 text-xs font-medium text-slate-600">
              {selectedColumns.length} columns · {effectiveSplits.length} splits
            </span>
          </div>
        </div>

        <div className="grid divide-y divide-slate-200 md:grid-cols-[minmax(0,0.9fr)_minmax(0,1.35fr)] md:divide-x md:divide-y-0">
          <section className="space-y-5 p-5">
            <label className="block text-sm font-medium text-slate-700">
              Snapshot
              <Select
                className="mt-1.5"
                value={activeSnapshotId}
                disabled={!data?.length}
                onChange={(event) => setSnapshotId(event.target.value)}
              >
                {!data?.length && <option value="">No ready snapshots</option>}
                {data?.map((item) => (
                  <option key={item.snapshot_id} value={item.snapshot_id}>
                    #{item.snapshot_id} · {item.name}
                  </option>
                ))}
              </Select>
            </label>

            <fieldset>
              <legend className="text-sm font-medium text-slate-700">File format</legend>
              <div className="mt-2 grid gap-2">
                {formatOptions.map((option) => (
                  <label
                    key={option.value}
                    className={`flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2.5 transition-colors ${
                      format === option.value
                        ? 'border-slate-800 bg-slate-900 text-white'
                        : 'border-slate-200 hover:border-slate-400'
                    }`}
                  >
                    <input
                      className="mt-1 accent-slate-950"
                      type="radio"
                      name="export-format"
                      value={option.value}
                      checked={format === option.value}
                      onChange={() => setFormat(option.value)}
                    />
                    <span>
                      <span className="block text-sm font-medium">{option.label}</span>
                      <span className={`block text-xs ${format === option.value ? 'text-slate-300' : 'text-slate-500'}`}>
                        {option.detail}
                      </span>
                    </span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className={`flex items-center gap-2 text-sm ${isHuggingFace ? 'text-slate-400' : 'text-slate-700'}`}>
              <input
                type="checkbox"
                className="accent-slate-900"
                checked={isHuggingFace || includeManifest}
                disabled={isHuggingFace}
                onChange={(event) => setIncludeManifest(event.target.checked)}
              />
              Include manifest.json
            </label>
          </section>

          <section className="space-y-5 p-5">
            <fieldset>
              <div className="flex items-baseline justify-between gap-3">
                <legend className="text-sm font-medium text-slate-700">Include columns</legend>
                <div className="flex gap-3 text-xs">
                  <button type="button" className="text-slate-600 underline hover:text-slate-950" onClick={() => setSelectedColumns([...allColumns])}>
                    All
                  </button>
                  <button type="button" className="text-slate-600 underline hover:text-slate-950" onClick={() => setSelectedColumns([...translationColumns])}>
                    Translation only
                  </button>
                </div>
              </div>
              <div className="mt-3 grid gap-x-5 gap-y-4 sm:grid-cols-2">
                {groupedColumns.map(({ group, columns: groupColumns }) => (
                  <div key={group}>
                    <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">{group}</div>
                    <div className="space-y-1.5">
                      {groupColumns.map((column) => (
                        <label key={column.id} className="flex items-center gap-2 text-sm text-slate-700">
                          <input
                            type="checkbox"
                            className="accent-slate-900"
                            checked={selectedColumns.includes(column.id)}
                            onChange={() => toggle(column.id, selectedColumns, setSelectedColumns)}
                          />
                          {column.label}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </fieldset>

            <fieldset>
              <legend className="text-sm font-medium text-slate-700">Splits</legend>
              <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
                {splitOptions.map((split) => (
                  <label key={split} className={`flex items-center gap-2 text-sm ${isHuggingFace ? 'text-slate-400' : 'text-slate-700'}`}>
                    <input
                      type="checkbox"
                      className="accent-slate-900"
                      checked={effectiveSplits.includes(split)}
                      disabled={isHuggingFace}
                      onChange={() => toggle(split, selectedSplits, setSelectedSplits)}
                    />
                    {split}
                  </label>
                ))}
              </div>
              {isHuggingFace && (
                <p className="mt-2 text-xs text-slate-500">
                  Hugging Face packages always include all three splits and a dataset card. After extracting, use <code>load_dataset('path/to/folder')</code>.
                </p>
              )}
            </fieldset>

            <a
              className={`inline-flex rounded-md px-4 py-2 text-sm font-medium ${
                customUrl ? 'bg-slate-900 text-white hover:bg-slate-700' : 'pointer-events-none bg-slate-200 text-slate-400'
              }`}
              href={customUrl}
              aria-disabled={!customUrl}
            >
              Download custom package (.zip)
            </a>
            {!selectedColumns.length && <p className="text-xs text-red-700">Choose at least one column to continue.</p>}
            {!effectiveSplits.length && <p className="text-xs text-red-700">Choose at least one split to continue.</p>}
          </section>
        </div>
      </Card>

      <h2 className="mb-3 text-sm font-semibold text-slate-700">Original snapshot files</h2>
      <DataTable
        loading={isLoading}
        rows={data as unknown as Row[]}
        empty="No ready snapshots yet."
        columns={[
          { key: 'snapshot_id', header: 'Snapshot', width: '90px' },
          { key: 'name', header: 'Name' },
          {
            key: 'counts',
            header: 'train / validation / test',
            render: (row) => {
              const counts = row.counts as Record<string, number>
              return `${counts?.train ?? 0} / ${counts?.validation ?? 0} / ${counts?.test ?? 0}`
            },
          },
          { key: 'prefix_uri', header: 'Location' },
          {
            key: 'download',
            header: 'Download as-is',
            render: (row) => ['train', 'validation', 'test', 'manifest'].map((file) => directLink(row.snapshot_id as number, file)),
          },
        ]}
      />
    </>
  )
}
