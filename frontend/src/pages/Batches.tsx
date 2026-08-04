import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, PageHeader } from '@/components/ui'
import { useList } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, Row } from '@/types'

export default function Batches() {
  const { data, isLoading } = useList<Batch>('batches')
  const [open, setOpen] = useState<number | null>(null)
  const rows = useQuery({
    queryKey: ['batch-rows', open],
    queryFn: () => api.get<Row[]>(`/batches/${open}/rows`, { limit: 25 }),
    enabled: open !== null,
  })

  return (
    <>
      <PageHeader title="Batches" subtitle="Immutable ingestions. Append only." />
      <DataTable
        loading={isLoading}
        rows={data as unknown as Row[]}
        empty="No batches yet — import a file first."
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'format', header: 'Format' },
          { key: 'sample_count', header: 'Samples' },
          { key: 'status', header: 'Status', render: (r) => <Badge>{String(r.status)}</Badge> },
          { key: 'parquet_uri', header: 'Parquet' },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <Button
                variant="ghost"
                onClick={() => setOpen(open === r.id ? null : (r.id as number))}
              >
                {open === r.id ? 'Hide' : 'Rows'}
              </Button>
            ),
          },
        ]}
      />

      {open !== null && (
        <Card className="mt-4">
          <h2 className="mb-3 font-medium">Batch {open} — first rows</h2>
          <DataTable
            loading={rows.isLoading}
            rows={rows.data}
            columns={[
              { key: 'sample_id', header: 'Sample' },
              { key: 'source_text', header: 'Source' },
              { key: 'target_text', header: 'Target' },
              { key: 'domain', header: 'Domain' },
              { key: 'quality', header: 'Quality' },
            ]}
          />
        </Card>
      )}
    </>
  )
}
