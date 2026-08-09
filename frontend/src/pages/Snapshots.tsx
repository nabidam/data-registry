import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'
import { CodeBlock } from '@astryxdesign/core/CodeBlock'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, useRemove } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Contamination, Dataset, Row, Snapshot, Split } from '@/types'

export default function Snapshots() {
  const snapshots = useQuery({
    queryKey: ['snapshots', undefined],
    queryFn: () => api.get<Snapshot[]>('/snapshots'),
    refetchInterval: (query) =>
      query.state.data?.some((s) => s.status === 'building') ? 2000 : false,
  })
  const datasets = useList<Dataset>('datasets', { limit: 200 })
  const splits = useList<Split>('splits', { limit: 200 })
  const create = useCreate<Snapshot>('snapshots')
  const remove = useRemove('snapshots')
  const [form, setForm] = useState({ name: '', dataset_id: '', split_id: '', seed: '42' })
  const [manifest, setManifest] = useState<Record<string, unknown> | null>(null)
  const [contamination, setContamination] = useState<Contamination[] | null>(null)

  return (
    <VStack gap={4}>
      <PageHeader
        title="Snapshots"
        subtitle="Immutable exports of trainable samples: train / validation / test + manifest"
      />
      <ErrorBox error={create.error} />

      <Card>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            create.mutate({
              name: form.name,
              dataset_id: Number(form.dataset_id),
              split_id: form.split_id ? Number(form.split_id) : null,
              seed: Number(form.seed),
            })
          }}
        >
          <Grid columns={5} gap={3} align="end">
            <Field label="Name">
              <Input
                required
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <Field label="Dataset">
              <Select
                required
                value={form.dataset_id}
                onChange={(e) => setForm({ ...form, dataset_id: e.target.value })}
              >
                <option value="">select…</option>
                {datasets.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Split">
              <Select
                value={form.split_id}
                onChange={(e) => setForm({ ...form, split_id: e.target.value })}
              >
                <option value="">default 90/5/5</option>
                {splits.data?.map((s) => (
                  <option key={s.id} value={s.id}>
                    #{s.id} {s.name} v{s.version}
                  </option>
                ))}
              </Select>
            </Field>
            <Field label="Seed (when no split)">
              <Input
                type="number"
                value={form.seed}
                onChange={(e) => setForm({ ...form, seed: e.target.value })}
              />
            </Field>
            <HStack vAlign="end">
              <Button disabled={create.isPending}>Build snapshot</Button>
            </HStack>
          </Grid>
        </form>
      </Card>

      <DataTable
        loading={snapshots.isLoading}
        rows={snapshots.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          { key: 'dataset_id', header: 'Dataset' },
          { key: 'split_id', header: 'Split' },
          { key: 'seed', header: 'Seed' },
          { key: 'status', header: 'Status', render: (r) => <Badge>{String(r.status)}</Badge> },
          {
            key: 'counts',
            header: 'Rows',
            render: (r) => {
              const counts = (r.stats as { counts?: Record<string, number> })?.counts
              return counts
                ? `${counts.train ?? 0} / ${counts.validation ?? 0} / ${counts.test ?? 0}`
                : (r.error as string) || '—'
            },
          },
          {
            key: 'actions',
            header: '',
            render: (r) => (
              <HStack gap={2}>
                <Button
                  variant="ghost"
                  onClick={async () =>
                    setManifest(await api.get<Record<string, unknown>>(`/snapshots/${r.id}/manifest`))
                  }
                >
                  Manifest
                </Button>
                <Button
                  variant="ghost"
                  onClick={async () =>
                    setContamination(
                      await api.get<Contamination[]>(`/snapshots/${r.id}/contamination`),
                    )
                  }
                >
                  Contamination
                </Button>
                <Button variant="danger" onClick={() => remove.mutate(r.id as number)}>
                  Delete
                </Button>
              </HStack>
            ),
          },
        ]}
      />

      {contamination && (
        <Card>
          <VStack gap={3}>
            <HStack vAlign="center" hAlign="between">
              <Heading level={4}>Historically contaminated samples</Heading>
              <Button variant="ghost" onClick={() => setContamination(null)}>
                Close
              </Button>
            </HStack>
            {contamination.length === 0 ? (
              <Text type="supporting" color="secondary">
                None: no sample in this snapshot was reserved for evaluation afterwards.
              </Text>
            ) : (
              <DataTable
                rows={contamination as unknown as Row[]}
                columns={[
                  { key: 'sample_count', header: 'Samples' },
                  { key: 'reason', header: 'Reason' },
                  { key: 'created_at', header: 'Detected' },
                ]}
              />
            )}
          </VStack>
        </Card>
      )}

      {manifest && (
        <Card>
          <VStack gap={3}>
            <HStack vAlign="center" hAlign="between">
              <Heading level={4}>manifest.json</Heading>
              <Button variant="ghost" onClick={() => setManifest(null)}>
                Close
              </Button>
            </HStack>
            <CodeBlock language="json" code={JSON.stringify(manifest, null, 2)} />
          </VStack>
        </Card>
      )}
    </VStack>
  )
}
