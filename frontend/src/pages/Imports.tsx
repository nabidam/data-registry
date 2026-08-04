import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useList } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, ReservationDefaults, Row, Source } from '@/types'

type Inspection = { format: string; columns: string[]; preview: Row[] }

export default function Imports() {
  const sources = useList<Source>('sources', { limit: 200 })
  const batches = useList<Batch>('batches', { limit: 10 })
  const qc = useQueryClient()

  const [file, setFile] = useState<File | null>(null)
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
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

  async function inspect(f: File) {
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
    const body = new FormData()
    body.append('file', file)
    Object.entries(form).forEach(([k, v]) => v && body.append(k, v))
    try {
      await api.upload<Batch>('/imports', body)
      qc.invalidateQueries({ queryKey: ['batches'] })
      setFile(null)
      setInspection(null)
      setForm({ ...form, batch_name: '' })
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  const columnSelect = (key: keyof typeof form, label: string, optional = false) => (
    <Field label={label}>
      <Select value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })}>
        {optional && <option value="">—</option>}
        {(inspection?.columns ?? [form[key]]).map((c) => (
          <option key={c} value={c}>
            {c}
          </option>
        ))}
      </Select>
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
            <Button disabled={busy || !file}>{busy ? 'Importing…' : 'Import'}</Button>
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
            header: 'Reserved',
            render: (r) =>
              String(
                (r.stats as { reservation?: { reserved?: number } })?.reservation?.reserved ?? '—',
              ),
          },
          { key: 'status', header: 'Status' },
        ]}
      />
    </>
  )
}
