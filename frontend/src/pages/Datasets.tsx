import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Button, Card, ErrorBox, Field, Input, PageHeader } from '@/components/ui'
import { useCreate, useList, useRemove } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, Dataset, Row } from '@/types'

const parseIds = (value: string): number[] =>
  value
    .split(',')
    .map((v) => Number(v.trim()))
    .filter((v) => Number.isFinite(v) && v > 0)

const parseList = (value: string): string[] =>
  value
    .split(',')
    .map((v) => v.trim())
    .filter(Boolean)

export default function Datasets() {
  const datasets = useList<Dataset>('datasets')
  const batches = useList<Batch>('batches', { limit: 200 })
  const create = useCreate<Dataset>('datasets')
  const remove = useRemove('datasets')
  const [form, setForm] = useState({
    name: '',
    description: '',
    batch_ids: '',
    src_langs: '',
    tgt_langs: '',
    domains: '',
    min_quality: '',
  })

  const [inspect, setInspect] = useState<number | null>(null)
  const stats = useQuery({
    queryKey: ['dataset-stats', inspect],
    queryFn: () => api.get<Record<string, unknown>>(`/datasets/${inspect}/statistics`),
    enabled: inspect !== null,
  })
  const preview = useQuery({
    queryKey: ['dataset-preview', inspect],
    queryFn: () => api.get<Row[]>(`/datasets/${inspect}/preview`, { limit: 20 }),
    enabled: inspect !== null,
  })

  return (
    <>
      <PageHeader
        title="Datasets"
        subtitle="Logical definitions — batches plus filters, no data copied"
      />
      <ErrorBox error={create.error} />

      <Card className="mb-4">
        <form
          className="grid gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({
              name: form.name,
              description: form.description || null,
              batch_ids: parseIds(form.batch_ids),
              filters: {
                src_langs: parseList(form.src_langs),
                tgt_langs: parseList(form.tgt_langs),
                domains: parseList(form.domains),
                min_quality: form.min_quality ? Number(form.min_quality) : null,
              },
            })
          }}
        >
          <Field label="Name">
            <Input
              required
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </Field>
          <Field label={`Batch ids (available: ${batches.data?.map((b) => b.id).join(', ') ?? '—'})`}>
            <Input
              placeholder="1,2  (empty = all)"
              value={form.batch_ids}
              onChange={(e) => setForm({ ...form, batch_ids: e.target.value })}
            />
          </Field>
          <Field label="Source langs">
            <Input
              placeholder="en"
              value={form.src_langs}
              onChange={(e) => setForm({ ...form, src_langs: e.target.value })}
            />
          </Field>
          <Field label="Target langs">
            <Input
              placeholder="fa"
              value={form.tgt_langs}
              onChange={(e) => setForm({ ...form, tgt_langs: e.target.value })}
            />
          </Field>
          <Field label="Domains">
            <Input
              placeholder="medical,legal"
              value={form.domains}
              onChange={(e) => setForm({ ...form, domains: e.target.value })}
            />
          </Field>
          <Field label="Min quality">
            <Input
              type="number"
              step="0.01"
              value={form.min_quality}
              onChange={(e) => setForm({ ...form, min_quality: e.target.value })}
            />
          </Field>
          <Field label="Description">
            <Input
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={create.isPending}>Create dataset</Button>
          </div>
        </form>
      </Card>

      <DataTable
        loading={datasets.isLoading}
        rows={datasets.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          {
            key: 'batch_ids',
            header: 'Batches',
            render: (r) => ((r.batch_ids as number[]).join(', ') || 'all'),
          },
          {
            key: 'filters',
            header: 'Filters',
            render: (r) => (
              <code className="text-xs">{JSON.stringify(r.filters).slice(0, 90)}</code>
            ),
          },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setInspect(r.id as number)}>
                  Inspect
                </Button>
                <Button variant="danger" onClick={() => remove.mutate(r.id as number)}>
                  Delete
                </Button>
              </div>
            ),
          },
        ]}
      />

      {inspect !== null && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-medium">Statistics</h2>
              <Button variant="ghost" onClick={() => setInspect(null)}>
                Close
              </Button>
            </div>
            <pre className="overflow-x-auto text-xs">
              {stats.isLoading ? 'Loading…' : JSON.stringify(stats.data, null, 2)}
            </pre>
          </Card>
          <Card>
            <h2 className="mb-2 font-medium">Preview</h2>
            <DataTable
              loading={preview.isLoading}
              rows={preview.data}
              columns={[
                { key: 'sample_id', header: 'ID' },
                { key: 'source_text', header: 'Source' },
                { key: 'target_text', header: 'Target' },
              ]}
            />
          </Card>
        </div>
      )}
    </>
  )
}
