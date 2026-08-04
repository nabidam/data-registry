import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, Field, Input, PageHeader, Select } from '@/components/ui'
import { api } from '@/lib/api'
import type { Allocation, Row, Sample } from '@/types'

type Query = {
  q: string
  batch_id: string
  src_lang: string
  tgt_lang: string
  domain: string
  allocation: string
}

const EMPTY: Query = { q: '', batch_id: '', src_lang: '', tgt_lang: '', domain: '', allocation: '' }

export default function Samples() {
  const [query, setQuery] = useState<Query>(EMPTY)
  const [applied, setApplied] = useState<Query>(EMPTY)
  const [page, setPage] = useState(0)
  const limit = 50

  // Text search hits Parquet through DuckDB; metadata-only listing hits Postgres.
  const searching = applied.q.trim().length > 0
  const list = useQuery<{ items: Row[]; total: number | null }>({
    queryKey: ['samples', applied, page],
    queryFn: async () => {
      if (searching) {
        const items = await api.get<Row[]>('/search', {
          ...applied,
          limit,
          offset: page * limit,
        })
        return { items, total: null }
      }
      const page_ = await api.get<{ items: Sample[]; total: number }>('/samples', {
        ...applied,
        q: undefined,
        limit,
        offset: page * limit,
      })
      return { items: page_.items as unknown as Row[], total: page_.total }
    },
  })

  const [selected, setSelected] = useState<number | null>(null)
  const detail = useQuery({
    queryKey: ['sample', selected],
    queryFn: () => api.get<Sample>(`/samples/${selected}`),
    enabled: selected !== null,
  })

  const qc = useQueryClient()
  // Reserving is one-way: a reserved sample can never return to the trainable pool.
  const reallocate = async (allocation: Allocation) => {
    if (selected === null) return
    await api.post('/samples/allocation', { sample_ids: [selected], allocation })
    await qc.invalidateQueries({ queryKey: ['sample'] })
    await qc.invalidateQueries({ queryKey: ['samples'] })
  }

  const field = (key: keyof Query, label: string) => (
    <Field label={label}>
      <Input value={query[key]} onChange={(e) => setQuery({ ...query, [key]: e.target.value })} />
    </Field>
  )

  return (
    <>
      <PageHeader
        title="Samples"
        subtitle="Immutable translation units. Allocation is decided at import time."
      />
      <Card className="mb-4">
        <form
          className="grid gap-3 md:grid-cols-7"
          onSubmit={(e) => {
            e.preventDefault()
            setPage(0)
            setApplied(query)
          }}
        >
          {field('q', 'Text contains')}
          {field('batch_id', 'Batch id')}
          {field('src_lang', 'Source lang')}
          {field('tgt_lang', 'Target lang')}
          {field('domain', 'Domain')}
          <Field label="Allocation">
            <Select
              value={query.allocation}
              onChange={(e) => setQuery({ ...query, allocation: e.target.value })}
            >
              <option value="">any</option>
              <option value="TRAINABLE">trainable</option>
              <option value="RESERVED_EVALUATION">reserved (evaluation)</option>
              <option value="IGNORED">ignored</option>
            </Select>
          </Field>
          <div className="flex items-end gap-2">
            <Button>Search</Button>
            <Button
              type="button"
              variant="ghost"
              onClick={() => {
                setQuery(EMPTY)
                setApplied(EMPTY)
              }}
            >
              Reset
            </Button>
          </div>
        </form>
      </Card>

      <DataTable
        loading={list.isLoading}
        rows={(list.data?.items ?? []) as unknown as Row[]}
        columns={
          searching
            ? [
                { key: 'sample_id', header: 'ID', width: '90px' },
                { key: 'source_text', header: 'Source' },
                { key: 'target_text', header: 'Target' },
                { key: 'domain', header: 'Domain' },
                { key: 'quality', header: 'Quality' },
              ]
            : [
                { key: 'id', header: 'ID', width: '90px' },
                { key: 'batch_id', header: 'Batch' },
                {
                  key: 'pair',
                  header: 'Pair',
                  render: (r) => `${r.src_lang}-${r.tgt_lang}`,
                },
                { key: 'domain', header: 'Domain' },
                { key: 'quality', header: 'Quality' },
                {
                  key: 'allocation',
                  header: 'Allocation',
                  render: (r) => <Badge>{String(r.allocation)}</Badge>,
                },
                {
                  key: 'actions',
                  header: '',
                  render: (r) => (
                    <Button variant="ghost" onClick={() => setSelected(r.id as number)}>
                      View
                    </Button>
                  ),
                },
              ]
        }
      />

      <div className="mt-3 flex items-center gap-3 text-sm text-slate-500">
        {list.data?.total != null && <span>{list.data.total} rows</span>}
        <Button variant="ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>
          Prev
        </Button>
        <span>page {page + 1}</span>
        <Button variant="ghost" onClick={() => setPage(page + 1)}>
          Next
        </Button>
      </div>

      {selected !== null && (
        <Card className="mt-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="font-medium">Sample {selected}</h2>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => void reallocate('RESERVED_EVALUATION')}>
                Reserve for evaluation
              </Button>
              <Button variant="ghost" onClick={() => void reallocate('IGNORED')}>
                Ignore
              </Button>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Close
              </Button>
            </div>
          </div>
          {detail.isLoading ? (
            <p className="text-sm text-slate-500">Loading…</p>
          ) : (
            <dl className="grid gap-2 text-sm md:grid-cols-2">
              <div>
                <dt className="text-slate-500">Source</dt>
                <dd>{detail.data?.source_text}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Target</dt>
                <dd>{detail.data?.target_text}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Batch / domain</dt>
                <dd>
                  {detail.data?.batch_id} / {detail.data?.domain ?? '—'}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Allocation / quality</dt>
                <dd>
                  {detail.data?.allocation} / {detail.data?.quality ?? '—'}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Reserved at</dt>
                <dd>{detail.data?.reserved_at ?? '—'}</dd>
              </div>
            </dl>
          )}
        </Card>
      )}
    </>
  )
}
