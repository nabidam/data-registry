import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { VStack } from '@astryxdesign/core/Stack'
import { Heading, Text } from '@astryxdesign/core/Text'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, Field, Input, PageHeader, Textarea } from '@/components/ui'
import { useList } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, BatchPurgeImpact, BatchPurgeResult, Row } from '@/types'

export default function Batches() {
  const { data, isLoading } = useList<Batch>('batches')
  const queryClient = useQueryClient()
  const [open, setOpen] = useState<number | null>(null)
  const [managed, setManaged] = useState<Batch | null>(null)
  const [confirmName, setConfirmName] = useState('')
  const [reason, setReason] = useState('')
  const rows = useQuery({
    queryKey: ['batch-rows', open],
    queryFn: () => api.get<Row[]>(`/batches/${open}/rows`, { limit: 25 }),
    enabled: open !== null,
  })
  const impact = useQuery({
    queryKey: ['batch-purge-impact', managed?.id, managed?.status],
    queryFn: () => api.get<BatchPurgeImpact>(`/batches/${managed?.id}/purge-impact`),
    enabled: managed !== null,
  })
  const refreshBatches = async () => {
    await queryClient.invalidateQueries({ queryKey: ['batches'] })
    await queryClient.invalidateQueries({ queryKey: ['batch-purge-impact'] })
  }
  const reject = useMutation({
    mutationFn: () => api.post<Batch>(`/batches/${managed?.id}/reject`, {
      confirm_name: confirmName,
      reason,
    }),
    onSuccess: async (batch) => {
      setManaged(batch)
      await refreshBatches()
    },
  })
  const restore = useMutation({
    mutationFn: () => api.post<Batch>(`/batches/${managed?.id}/restore`, {
      confirm_name: confirmName,
    }),
    onSuccess: async (batch) => {
      setManaged(batch)
      await refreshBatches()
    },
  })
  const purge = useMutation({
    mutationFn: () => api.post<BatchPurgeResult>(`/batches/${managed?.id}/purge`, {
      confirm_name: confirmName,
      reason,
    }),
    onSuccess: async () => {
      setManaged(null)
      setConfirmName('')
      setReason('')
      await refreshBatches()
    },
  })

  const manage = (batch: Batch) => {
    setManaged(batch)
    setConfirmName('')
    setReason('')
    reject.reset()
    restore.reset()
    purge.reset()
  }

  return (
    <VStack gap={4}>
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
          {
            key: 'evaluation',
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
          { key: 'status', header: 'Status', render: (r) => <Badge>{String(r.status)}</Badge> },
          { key: 'parquet_uri', header: 'Parquet' },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <div className="flex items-center gap-2">
                <Button
                  variant="ghost"
                  onClick={() => setOpen(open === r.id ? null : (r.id as number))}
                >
                  {open === r.id ? 'Hide' : 'Rows'}
                </Button>
                <Button variant="ghost" onClick={() => manage(r as unknown as Batch)}>
                  Manage
                </Button>
              </div>
            ),
          },
        ]}
      />

      {open !== null && (
        <Card>
          <VStack gap={3}>
            <Heading level={4}>Batch {open} — first rows</Heading>
            <DataTable
              loading={rows.isLoading}
              rows={rows.data}
              columns={[
                { key: 'sample_id', header: 'Sample' },
                { key: 'source_text', header: 'Source' },
                { key: 'target_text', header: 'Target' },
                { key: 'domain', header: 'Domain' },
                { key: 'quality', header: 'Quality' },
                { key: 'evaluation_split', header: 'Eval split' },
                {
                  key: 'human_verify',
                  header: 'Human verify',
                  render: (r) => (r.human_verify === true ? 'yes' : '—'),
                },
              ]}
            />
          </VStack>
        </Card>
      )}

      {managed !== null && (
        <Card
          style={{
            borderColor: 'rgba(239, 68, 68, 0.42)',
            backgroundColor: 'rgba(127, 29, 29, 0.08)',
          }}
        >
          <VStack gap={4}>
            <div className="flex items-start justify-between gap-4">
              <VStack gap={1}>
                <div className="flex items-center gap-2">
                  <Heading level={3}>Batch safety controls</Heading>
                  <Badge>{managed.status}</Badge>
                </div>
                <Text type="supporting" color="secondary">
                  Batch #{managed.id} · {managed.name} · {managed.sample_count.toLocaleString()} samples
                </Text>
              </VStack>
              <Button variant="ghost" onClick={() => setManaged(null)}>Close</Button>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Type the batch name to confirm">
                <Input
                  value={confirmName}
                  placeholder={managed.name}
                  onChange={(event) => setConfirmName(event.target.value)}
                />
              </Field>
              <Field label="Reason">
                <Textarea
                  rows={3}
                  value={reason}
                  placeholder="Describe the import problem and why this action is needed."
                  onChange={(event) => setReason(event.target.value)}
                />
              </Field>
            </div>

            <ErrorBox error={reject.error || restore.error || purge.error || impact.error} />

            {impact.data && (
              <div className="grid gap-3 md:grid-cols-2">
                <Card padding={3} style={{ backgroundColor: 'rgba(15, 23, 42, 0.45)' }}>
                  <VStack gap={2}>
                    <Text type="label" weight="semibold">PURGE DRY RUN</Text>
                    <Badge variant={impact.data.can_purge ? 'success' : 'error'}>
                      {impact.data.can_purge ? 'eligible' : 'blocked'}
                    </Badge>
                    <Text type="supporting" color="secondary">
                      Storage scope: {impact.data.storage_prefixes.join(' and ')}
                    </Text>
                  </VStack>
                </Card>
                <Card padding={3} style={{ backgroundColor: 'rgba(15, 23, 42, 0.45)' }}>
                  <VStack gap={2}>
                    <Text type="label" weight="semibold">DEPENDENCIES</Text>
                    {impact.data.blockers.length === 0 && impact.data.warnings.length === 0 && (
                      <Text type="supporting" color="secondary">No downstream references found.</Text>
                    )}
                    {impact.data.blockers.map((blocker) => (
                      <Text key={blocker} type="supporting" style={{ color: '#fca5a5' }}>
                        Blocker: {blocker}
                      </Text>
                    ))}
                    {impact.data.warnings.map((warning) => (
                      <Text key={warning} type="supporting" style={{ color: '#fbbf24' }}>
                        Warning: {warning}
                      </Text>
                    ))}
                  </VStack>
                </Card>
              </div>
            )}

            <div className="flex flex-wrap items-center gap-2 border-t border-red-900/40 pt-4">
              {managed.status === 'ready' && (
                <Button
                  variant="danger"
                  disabled={confirmName !== managed.name || reason.trim().length < 3 || reject.isPending}
                  onClick={() => reject.mutate()}
                >
                  {reject.isPending ? 'Rejecting…' : 'Reject batch'}
                </Button>
              )}
              {managed.status === 'rejected' && (
                <Button
                  variant="ghost"
                  disabled={confirmName !== managed.name || restore.isPending}
                  onClick={() => restore.mutate()}
                >
                  {restore.isPending ? 'Restoring…' : 'Restore to ready'}
                </Button>
              )}
              {(managed.status === 'rejected' || managed.status === 'failed') && (
                <Button
                  variant="danger"
                  disabled={
                    confirmName !== managed.name ||
                    reason.trim().length < 10 ||
                    !impact.data?.can_purge ||
                    purge.isPending
                  }
                  onClick={() => purge.mutate()}
                >
                  {purge.isPending ? 'Purging…' : 'Permanently purge'}
                </Button>
              )}
              <Text type="supporting" color="secondary">
                Reject is reversible. Purge removes the batch's metadata and objects.
              </Text>
            </div>
          </VStack>
        </Card>
      )}
    </VStack>
  )
}
