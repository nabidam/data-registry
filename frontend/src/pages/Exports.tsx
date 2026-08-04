import { useQuery } from '@tanstack/react-query'

import { DataTable } from '@/components/DataTable'
import { PageHeader } from '@/components/ui'
import { api } from '@/lib/api'
import type { Row } from '@/types'

type Export = {
  snapshot_id: number
  name: string
  prefix_uri: string | null
  counts: Record<string, number>
  created_at: string
}

export default function Exports() {
  const { data, isLoading } = useQuery({
    queryKey: ['exports'],
    queryFn: () => api.get<Export[]>('/exports'),
  })

  const link = (id: number, file: string) => (
    <a
      key={file}
      className="mr-3 text-sm text-slate-700 underline"
      href={api.downloadUrl(`/exports/${id}/${file}`)}
    >
      {file}
    </a>
  )

  return (
    <>
      <PageHeader title="Exports" subtitle="Ready snapshots, downloadable as Parquet + manifest" />
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
            render: (r) => {
              const c = r.counts as Record<string, number>
              return `${c?.train ?? 0} / ${c?.validation ?? 0} / ${c?.test ?? 0}`
            },
          },
          { key: 'prefix_uri', header: 'Location' },
          {
            key: 'download',
            header: 'Download',
            render: (r) =>
              ['train', 'validation', 'test', 'manifest'].map((f) =>
                link(r.snapshot_id as number, f),
              ),
          },
        ]}
      />
    </>
  )
}
