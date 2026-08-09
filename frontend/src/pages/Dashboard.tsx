import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'
import { Banner } from '@astryxdesign/core/Banner'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, PageHeader, Stat } from '@/components/ui'
import { api } from '@/lib/api'
import type { Batch, Overview, Snapshot } from '@/types'

export default function Dashboard() {
  const navigate = useNavigate()

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
  const isLoading = overview.isLoading
  const error = overview.error || batches.error || snapshots.error

  return (
    <VStack gap={5}>
      <PageHeader
        title="Dashboard"
        subtitle={
          o?.samples
            ? `MT Registry Active • ${(o.samples / 1_000_000).toFixed(1)}M Translation Pairs`
            : 'Machine Translation Dataset Registry Overview'
        }
        actions={
          <HStack gap={2} vAlign="center">
            <Button variant="primary" label="Import Batch" onClick={() => navigate('/imports')} />
            <Button variant="ghost" label="New Dataset" onClick={() => navigate('/datasets')} />
            <Button variant="ghost" label="Export Snapshot" onClick={() => navigate('/snapshots')} />
          </HStack>
        }
      />

      {error && (
        <VStack gap={2}>
          <ErrorBox error={error} />
          <HStack hAlign="end">
            <Button
              variant="ghost"
              label="Retry Fetching Dashboard Data"
              onClick={() => {
                overview.refetch()
                batches.refetch()
                snapshots.refetch()
              }}
            />
          </HStack>
        </VStack>
      )}

      {o?.contaminated_snapshots ? (
        <Banner
          status="warning"
          title={`${o.contaminated_snapshots} Contaminated Snapshot${o.contaminated_snapshots > 1 ? 's' : ''} Detected`}
          description="Evaluation benchmark samples have leaked into training splits. Quarantine or exclude these snapshots before running model training."
          endContent={
            <Button
              variant="primary"
              label="Triage Contamination"
              onClick={() => navigate('/snapshots')}
            />
          }
        />
      ) : null}

      {/* Group 1: Dataset Scale & Storage Inventory */}
      <VStack gap={2}>
        <HStack vAlign="center" hAlign="between">
          <Text type="supporting" color="secondary" weight="medium">
            DATASET SCALE & STORAGE INVENTORY
          </Text>
          <Text type="supporting" color="secondary">
            Parquet Storage on S3/MinIO
          </Text>
        </HStack>
        <Grid columns={3} gap={3}>
          <Stat label="Translation Pairs" value={o?.samples ? o.samples.toLocaleString() : '—'} loading={isLoading} />
          <Stat label="Ingestion Batches" value={o?.batches ? o.batches.toLocaleString() : '—'} loading={isLoading} />
          <Stat label="Logical Datasets" value={o?.datasets ? o.datasets.toLocaleString() : '—'} loading={isLoading} />
          <Stat label="Immutable Snapshots" value={o?.snapshots ? o.snapshots.toLocaleString() : '—'} loading={isLoading} />
          <Stat label="Data Sources" value={o?.sources ? o.sources.toLocaleString() : '—'} loading={isLoading} />
          <Stat label="Evaluation Sets" value={o?.evaluation_sets ? o.evaluation_sets.toLocaleString() : '—'} loading={isLoading} />
        </Grid>
      </VStack>

      {/* Group 2: Sample Allocation Pipeline */}
      <VStack gap={2}>
        <HStack vAlign="center" hAlign="between">
          <Text type="supporting" color="secondary" weight="medium">
            SAMPLE ALLOCATION PIPELINE
          </Text>
          <Text type="supporting" color="secondary">
            Immutable 1-to-1 Sample Allocation Lifecycle
          </Text>
        </HStack>
        <Grid columns={4} gap={3}>
          <Stat
            label="Trainable Samples"
            value={o?.trainable_samples ? o.trainable_samples.toLocaleString() : '—'}
            loading={isLoading}
          />
          <Stat
            label="Reserved (Eval)"
            value={o?.reserved_samples ? o.reserved_samples.toLocaleString() : '—'}
            loading={isLoading}
          />
          <Stat
            label="Quarantined Samples"
            value={o?.quarantined_samples ? o.quarantined_samples.toLocaleString() : '—'}
            loading={isLoading}
            variant={o?.quarantined_samples ? 'warning' : 'normal'}
          />
          <Stat
            label="Ignored Samples"
            value={o?.ignored_samples ? o.ignored_samples.toLocaleString() : '—'}
            loading={isLoading}
          />
        </Grid>
      </VStack>

      {/* Group 3: Model Experiments & Safety Integrity */}
      <VStack gap={2}>
        <HStack vAlign="center" hAlign="between">
          <Text type="supporting" color="secondary" weight="medium">
            EXPERIMENTS & PIPELINE INTEGRITY
          </Text>
          <Text type="supporting" color="secondary">
            Benchmark Leakage & Safety Tracking
          </Text>
        </HStack>
        <Grid columns={2} gap={3}>
          <Stat label="Active Experiments" value={o?.experiments ? o.experiments.toLocaleString() : '—'} loading={isLoading} />
          <Stat
            label="Contaminated Snapshots"
            value={o?.contaminated_snapshots ?? '0'}
            loading={isLoading}
            variant={o?.contaminated_snapshots ? 'danger' : 'normal'}
          />
        </Grid>
      </VStack>

      {/* Activity Tables */}
      <Grid columns={2} gap={4}>
        <Card>
          <VStack gap={3}>
            <HStack vAlign="center" hAlign="between">
              <Heading level={3}>Recent Batches</Heading>
              <Link
                className="text-xs font-medium text-slate-400 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded px-1.5 py-0.5 transition-colors"
                to="/batches"
                aria-label="View all ingestion batches"
              >
                All Batches →
              </Link>
            </HStack>
            <DataTable<Batch>
              loading={batches.isLoading}
              rows={batches.data}
              columns={[
                { key: 'name', header: 'Name' },
                {
                  key: 'sample_count',
                  header: 'Samples',
                  render: (r) => (r.sample_count ? r.sample_count.toLocaleString() : '0'),
                },
                {
                  key: 'status',
                  header: 'Status',
                  render: (r) => <Badge>{r.status}</Badge>,
                },
              ]}
            />
          </VStack>
        </Card>

        <Card>
          <VStack gap={3}>
            <HStack vAlign="center" hAlign="between">
              <Heading level={3}>Recent Snapshots</Heading>
              <Link
                className="text-xs font-medium text-slate-400 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 rounded px-1.5 py-0.5 transition-colors"
                to="/snapshots"
                aria-label="View all immutable snapshots"
              >
                All Snapshots →
              </Link>
            </HStack>
            <DataTable<Snapshot>
              loading={snapshots.isLoading}
              rows={snapshots.data}
              columns={[
                { key: 'name', header: 'Name' },
                {
                  key: 'status',
                  header: 'Status',
                  render: (r) => <Badge>{r.status}</Badge>,
                },
                {
                  key: 'total',
                  header: 'Translation Pairs',
                  render: (r) => (r.stats?.total ? r.stats.total.toLocaleString() : '—'),
                },
              ]}
            />
          </VStack>
        </Card>
      </Grid>
    </VStack>
  )
}
