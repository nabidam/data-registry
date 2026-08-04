import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useList } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, ReservationDefaults, Row, Source } from '@/types'

type Inspection = { format: string; columns: string[]; preview: Row[] }
type UploadStart = { batch: Batch; upload_id: string; part_size: number }
type UploadedPart = { part_number: number; etag: string }

const INSPECTION_LIMIT_BYTES = 16 * 1024 * 1024

export default function Imports() {
  const sources = useList<Source>('sources', { limit: 200 })
  const batches = useList<Batch>('batches', { limit: 10 })
  const qc = useQueryClient()

  const [file, setFile] = useState<File | null>(null)
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [form, setForm] = useState({
    batch_name: '',
    src_lang: 'en',
    tgt_lang: 'fa',
    source_id: '',
    domain: '',
    source_column: 'source',
    target_column: 'target',
    domain_column: '',
    quality_column: '',
    document_id_column: '',
    notes: '',
    // Evaluation reservation, applied to this import before anything is trainable.
    evaluation_percent: '',
    evaluation_max_samples: '',
    evaluation_selector: '',
    random_seed: '',
  })

  // Blank reservation fields fall back to these server-side defaults.
  const defaults = useQuery({
    queryKey: ['reservation-defaults'],
    queryFn: () => api.get<ReservationDefaults>('/imports/reservation-defaults'),
  })
  const effectiveSelector = form.evaluation_selector || defaults.data?.evaluation_selector

  async function inspect(f: File) {
    // Sending a multi-GB file merely to discover its first few columns was the
    // first source of gateway failures. Large imports use the editable defaults
    // below; a small representative file can still be inspected normally.
    if (f.size > INSPECTION_LIMIT_BYTES) {
      setInspection(null)
      return
    }
    setError(null)
    const body = new FormData()
    body.append('file', f)
    try {
      const result = await api.upload<Inspection>('/imports/inspect', body)
      setInspection(result)
      // Pre-fill the mapping when the file already uses canonical names.
      setForm((prev) => ({
        ...prev,
        source_column: result.columns.includes(prev.source_column)
          ? prev.source_column
          : (result.columns[0] ?? ''),
        target_column: result.columns.includes(prev.target_column)
          ? prev.target_column
          : (result.columns[1] ?? ''),
      }))
    } catch (e) {
      setError(e)
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!file) return
    setBusy(true)
    setError(null)
    try {
      const started = await api.post<UploadStart>('/imports/start', {
        batch_name: form.batch_name,
        filename: file.name,
        size: file.size,
      })
      const parts: UploadedPart[] = []
      const totalParts = Math.ceil(file.size / started.part_size)
      for (let index = 0; index < totalParts; index += 1) {
        const body = new FormData()
        body.append('upload_id', started.upload_id)
        // A named File keeps the raw object key stable across every part.
        body.append(
          'file',
          new File([file.slice(index * started.part_size, (index + 1) * started.part_size)], file.name, {
            type: file.type,
          }),
        )
        const part = await api.upload<UploadedPart>(
          `/imports/${started.batch.id}/parts/${index + 1}`,
          body,
        )
        parts.push(part)
        setUploadProgress((index + 1) / totalParts)
      }
      const complete = Object.fromEntries(
        Object.entries(form).filter(([, value]) => value !== ''),
      )
      await api.post<Batch>(`/imports/${started.batch.id}/complete`, { ...complete, parts })
      qc.invalidateQueries({ queryKey: ['batches'] })
      setFile(null)
      setInspection(null)
      setForm({ ...form, batch_name: '' })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
      setUploadProgress(null)
    }
  }

  const columnSelect = (key: keyof typeof form, label: string, optional = false) => (
    <Field label={label}>
      {inspection ? (
        <Select value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })}>
          {optional && <option value="">—</option>}
          {inspection.columns.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
      ) : (
        <Input
          value={form[key]}
          placeholder={optional ? 'Optional column name' : undefined}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        />
      )}
    </Field>
  )

  return (
    <>
      <PageHeader
        title="Imports"
        subtitle="Each import creates one immutable batch; a slice is reserved for evaluation before any of it becomes trainable"
      />
      <ErrorBox error={error} />

      <Card className="mb-4">
        <form className="grid gap-3 md:grid-cols-4" onSubmit={submit}>
          <Field label="File">
            <Input
              type="file"
              required
              onChange={(e) => {
                const f = e.target.files?.[0] ?? null
                setFile(f)
                if (f) void inspect(f)
              }}
            />
          </Field>
          <Field label="Batch name">
            <Input
              required
              value={form.batch_name}
              onChange={(e) => setForm({ ...form, batch_name: e.target.value })}
            />
          </Field>
          <Field label="Source language">
            <Input
              required
              value={form.src_lang}
              onChange={(e) => setForm({ ...form, src_lang: e.target.value })}
            />
          </Field>
          <Field label="Target language">
            <Input
              required
              value={form.tgt_lang}
              onChange={(e) => setForm({ ...form, tgt_lang: e.target.value })}
            />
          </Field>
          <Field label="Source (origin)">
            <Select
              value={form.source_id}
              onChange={(e) => setForm({ ...form, source_id: e.target.value })}
            >
              <option value="">—</option>
              {sources.data?.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Default domain">
            <Input
              value={form.domain}
              onChange={(e) => setForm({ ...form, domain: e.target.value })}
            />
          </Field>
          {columnSelect('source_column', 'Source text column')}
          {columnSelect('target_column', 'Target text column')}
          {columnSelect('domain_column', 'Domain column', true)}
          {columnSelect('quality_column', 'Quality column', true)}
          {columnSelect('document_id_column', 'Document ID column', true)}
          <Field label={`Evaluation % (default ${defaults.data?.evaluation_percent ?? '—'})`}>
            <Input
              type="number"
              step="0.1"
              placeholder={String(defaults.data?.evaluation_percent ?? '')}
              value={form.evaluation_percent}
              onChange={(e) => setForm({ ...form, evaluation_percent: e.target.value })}
            />
          </Field>
          <Field label={`Max reserved (default ${defaults.data?.evaluation_max_samples ?? '—'})`}>
            <Input
              type="number"
              placeholder={String(defaults.data?.evaluation_max_samples ?? '')}
              value={form.evaluation_max_samples}
              onChange={(e) => setForm({ ...form, evaluation_max_samples: e.target.value })}
            />
          </Field>
          <Field label="Selector">
            <Select
              value={form.evaluation_selector}
              onChange={(e) => setForm({ ...form, evaluation_selector: e.target.value })}
            >
              <option value="">{defaults.data?.evaluation_selector ?? 'default'}</option>
              {defaults.data?.selectors.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </Select>
          </Field>
          {effectiveSelector === 'contamination_safe' && (
            <p className="text-sm text-muted-foreground md:col-span-4">
              {defaults.data?.policies.contamination_safe.description}{' '}
              {defaults.data?.policies.contamination_safe.contamination.document_level_holdout ===
              true
                ? 'Document holdout is enabled.'
                : 'Document holdout is disabled.'}
            </p>
          )}
          <Field label={`Seed (default ${defaults.data?.random_seed ?? '—'})`}>
            <Input
              type="number"
              placeholder={String(defaults.data?.random_seed ?? '')}
              value={form.random_seed}
              onChange={(e) => setForm({ ...form, random_seed: e.target.value })}
            />
          </Field>
          <Field label="Notes">
            <Input
              value={form.notes}
              onChange={(e) => setForm({ ...form, notes: e.target.value })}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={busy || !file}>
              {uploadProgress === null
                ? busy
                  ? 'Preparing import…'
                  : 'Import'
                : `Uploading ${Math.round(uploadProgress * 100)}%`}
            </Button>
          </div>
        </form>
      </Card>

      {inspection && (
        <Card className="mb-4">
          <h2 className="mb-2 font-medium">
            Detected format: <code>{inspection.format}</code>
          </h2>
          <DataTable
            rows={inspection.preview}
            columns={inspection.columns.map((c) => ({ key: c, header: c }))}
          />
        </Card>
      )}

      <h2 className="mb-2 font-medium">Recent batches</h2>
      <DataTable
        loading={batches.isLoading}
        rows={batches.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'format', header: 'Format' },
          { key: 'sample_count', header: 'Samples' },
          {
            key: 'reserved',
            header: 'Evaluation',
            render: (r) =>
              String(
                (r.stats as { reservation?: { reserved?: number } })?.reservation?.reserved ?? '—',
              ),
          },
          {
            key: 'quarantined',
            header: 'Quarantined',
            render: (r) =>
              String(
                (r.stats as { reservation?: { quarantined?: number } })?.reservation?.quarantined ??
                  0,
              ),
          },
          {
            key: 'splits',
            header: 'Dev / test',
            render: (r) => {
              const splits = (r.stats as { reservation?: { selection?: { splits?: Record<string, number> } } })
                ?.reservation?.selection?.splits
              return splits ? `${splits.dev ?? 0} / ${splits.test ?? 0}` : '—'
            },
          },
          {
            key: 'gold',
            header: 'Human verify',
            render: (r) =>
              String(
                (r.stats as { reservation?: { selection?: { human_verify?: number } } })
                  ?.reservation?.selection?.human_verify ?? '—',
              ),
          },
          { key: 'status', header: 'Status' },
        ]}
      />
    </>
  )
}
