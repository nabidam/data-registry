import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, useRemove } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { EvaluationSet, Row } from '@/types'

const parseList = (v: string) => v.split(',').map((s) => s.trim()).filter(Boolean)

export default function EvaluationSets() {
  const sets = useList<EvaluationSet>('evaluation-sets')
  const create = useCreate<EvaluationSet>('evaluation-sets')
  const remove = useRemove('evaluation-sets')
  const [form, setForm] = useState({
    name: '',
    kind: 'sampled',
    domains: '',
    src_langs: '',
    limit: '500',
    sample_ids: '',
    seed: '42',
  })
  const [open, setOpen] = useState<number | null>(null)
  const rows = useQuery({
    queryKey: ['eval-rows', open],
    queryFn: () => api.get<Row[]>(`/evaluation-sets/${open}/rows`, { limit: 20 }),
    enabled: open !== null,
  })

  return (
    <>
      <PageHeader
        title="Evaluation Sets"
        subtitle="Benchmark collections, independent from train/test splits"
      />
      <ErrorBox error={create.error} />

      <Card className="mb-4">
        <form
          className="grid gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault()
            const ids = parseList(form.sample_ids).map(Number).filter(Number.isFinite)
            create.mutate({
              name: form.name,
              kind: form.kind,
              seed: Number(form.seed),
              limit: ids.length ? null : Number(form.limit),
              sample_ids: ids.length ? ids : null,
              filters: { domains: parseList(form.domains), src_langs: parseList(form.src_langs) },
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
          <Field label="Kind">
            <Select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="sampled">sampled</option>
              <option value="manual">manual</option>
              <option value="imported">imported</option>
            </Select>
          </Field>
          <Field label="Domains">
            <Input
              placeholder="medical"
              value={form.domains}
              onChange={(e) => setForm({ ...form, domains: e.target.value })}
            />
          </Field>
          <Field label="Source langs">
            <Input
              value={form.src_langs}
              onChange={(e) => setForm({ ...form, src_langs: e.target.value })}
            />
          </Field>
          <Field label="Limit (sampled)">
            <Input
              type="number"
              value={form.limit}
              onChange={(e) => setForm({ ...form, limit: e.target.value })}
            />
          </Field>
          <Field label="Sample ids (manual/imported)">
            <Input
              placeholder="12,44,91"
              value={form.sample_ids}
              onChange={(e) => setForm({ ...form, sample_ids: e.target.value })}
            />
          </Field>
          <Field label="Seed">
            <Input
              type="number"
              value={form.seed}
              onChange={(e) => setForm({ ...form, seed: e.target.value })}
            />
          </Field>
          <div className="flex items-end">
            <Button disabled={create.isPending}>Build set</Button>
          </div>
        </form>
      </Card>

      <DataTable
        loading={sets.isLoading}
        rows={sets.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'kind', header: 'Kind' },
          { key: 'sample_count', header: 'Samples' },
          { key: 'parquet_uri', header: 'Parquet' },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <div className="flex gap-2">
                <Button variant="ghost" onClick={() => setOpen(r.id as number)}>
                  Rows
                </Button>
                <Button variant="danger" onClick={() => remove.mutate(r.id as number)}>
                  Delete
                </Button>
              </div>
            ),
          },
        ]}
      />

      {open !== null && (
        <Card className="mt-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-medium">Evaluation set {open}</h2>
            <Button variant="ghost" onClick={() => setOpen(null)}>
              Close
            </Button>
          </div>
          <DataTable
            loading={rows.isLoading}
            rows={rows.data}
            columns={[
              { key: 'sample_id', header: 'ID' },
              { key: 'source_text', header: 'Source' },
              { key: 'target_text', header: 'Target' },
              { key: 'domain', header: 'Domain' },
            ]}
          />
        </Card>
      )}
    </>
  )
}
