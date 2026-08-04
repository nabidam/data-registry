import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { DataTable } from '@/components/DataTable'
import { Card, PageHeader, Stat } from '@/components/ui'
import { api } from '@/lib/api'
import type { Batch, Overview, Snapshot } from '@/types'

export default function Dashboard() {
  const overview = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/stats/overview'),
  })
  const batches = useQuery({
    queryKey: ['batches', 'recent'],
    queryFn: () => api.get<Batch[]>('/batches', { limit: 5 }),
  })
  const snapshots = useQuery({
    queryKey: ['snapshots', 'recent'],
    queryFn: () => api.get<Snapshot[]>('/snapshots', { limit: 5 }),
  })

  const o = overview.data
  return (
    <>
      <PageHeader title="Dashboard" subtitle="Registry at a glance" />
      <div className="mb-6 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="Samples" value={o?.samples ?? '—'} />
        <Stat label="Batches" value={o?.batches ?? '—'} />
        <Stat label="Datasets" value={o?.datasets ?? '—'} />
        <Stat label="Snapshots" value={o?.snapshots ?? '—'} />
        <Stat label="Sources" value={o?.sources ?? '—'} />
        <Stat label="Evaluation sets" value={o?.evaluation_sets ?? '—'} />
        <Stat label="Experiments" value={o?.experiments ?? '—'} />
        <Stat label="Trainable" value={o?.trainable_samples ?? '—'} />
        <Stat label="Reserved (eval)" value={o?.reserved_samples ?? '—'} />
        <Stat label="Ignored" value={o?.ignored_samples ?? '—'} />
        <Stat label="Contaminated snapshots" value={o?.contaminated_snapshots ?? '—'} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-medium">Recent batches</h2>
            <Link className="text-sm text-slate-500 hover:underline" to="/batches">
              All batches
            </Link>
          </div>
          <DataTable
            loading={batches.isLoading}
            rows={batches.data as unknown as Record<string, unknown>[]}
            columns={[
              { key: 'name', header: 'Name' },
              { key: 'sample_count', header: 'Samples' },
              { key: 'status', header: 'Status' },
            ]}
          />
        </Card>
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-medium">Recent snapshots</h2>
            <Link className="text-sm text-slate-500 hover:underline" to="/snapshots">
              All snapshots
            </Link>
          </div>
          <DataTable
            loading={snapshots.isLoading}
            rows={snapshots.data as unknown as Record<string, unknown>[]}
            columns={[
              { key: 'name', header: 'Name' },
              { key: 'status', header: 'Status' },
              {
                key: 'total',
                header: 'Rows',
                render: (r) => String((r.stats as { total?: number })?.total ?? '—'),
              },
            ]}
          />
        </Card>
      </div>
    </>
  )
}
