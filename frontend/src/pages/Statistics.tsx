import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'

import { DataTable, type Column } from '@/components/DataTable'
import { Badge, Card, ErrorBox, PageHeader, Stat } from '@/components/ui'
import { api } from '@/lib/api'
import type { Overview, Row } from '@/types'

function formatHeader(header: string): string {
  const map: Record<string, string> = {
    src_lang: 'Source Language',
    tgt_lang: 'Target Language',
    count: 'Sample Count',
    domain: 'Domain Name',
    id: 'ID',
    name: 'Name',
    sample_count: 'Sample Count',
    source_id: 'Source ID',
    total_samples: 'Total Samples',
    allocation: 'Allocation State',
  }
  return map[header] || header.replace(/_/g, ' ').replace(/\b\w/g, (l) => l.toUpperCase())
}

function StatTable<T extends Row>({
  title,
  subtitle,
  path,
  columns,
}: {
  title: string
  subtitle?: string
  path: string
  columns: Column<T>[]
}) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['stats', path],
    queryFn: () => api.get<T[]>(path),
  })

  return (
    <Card>
      <VStack gap={3}>
        <VStack gap={0.5}>
          <Heading level={4}>{title}</Heading>
          {subtitle && (
            <Text type="supporting" color="secondary">
              {subtitle}
            </Text>
          )}
        </VStack>
        {isError ? (
          <ErrorBox error={error} />
        ) : (
          <DataTable
            loading={isLoading}
            rows={data}
            columns={columns.map((c) => ({
              ...c,
              header: formatHeader(c.header),
            }))}
          />
        )}
      </VStack>
    </Card>
  )
}

export default function Statistics() {
  const overviewQuery = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<Overview>('/stats/overview'),
  })

  const o = overviewQuery.data
  const isOverviewLoading = overviewQuery.isLoading

  return (
    <VStack gap={5}>
      <PageHeader
        title="Registry Statistics"
        subtitle="Comprehensive breakdown of translation pairs, domains, allocations, provenance sources, ingestion batches, dataset definitions, snapshots, and evaluation benchmarks."
      />

      {overviewQuery.isError && <ErrorBox error={overviewQuery.error} />}

      {/* Top Overview KPI Metrics */}
      <Grid columns={4} gap={4}>
        <Stat
          label="Total Translation Pairs"
          value={o?.samples != null ? o.samples.toLocaleString() : '0'}
          loading={isOverviewLoading}
        />
        <Stat
          label="Trainable Samples"
          value={o?.trainable_samples != null ? o.trainable_samples.toLocaleString() : '0'}
          loading={isOverviewLoading}
        />
        <Stat
          label="Reserved Evaluation"
          value={o?.reserved_samples != null ? o.reserved_samples.toLocaleString() : '0'}
          loading={isOverviewLoading}
          variant="warning"
        />
        <Stat
          label="Active Datasets & Snapshots"
          value={
            o ? `${o.datasets ?? 0} Datasets / ${o.snapshots ?? 0} Snapshots` : '0'
          }
          loading={isOverviewLoading}
        />
      </Grid>

      {/* Detailed Entity Statistics Grid */}
      <Grid columns={2} gap={4}>
        {/* Language Pairs */}
        <StatTable
          title="By Language Pair"
          subtitle="Distribution of translation directions"
          path="/stats/language-pairs"
          columns={[
            {
              key: 'pair',
              header: 'Language Pair',
              render: (row) => (
                <Link
                  to={`/samples?src_lang=${encodeURIComponent(String(row.src_lang))}&tgt_lang=${encodeURIComponent(String(row.tgt_lang))}`}
                  className="inline-flex items-center gap-1.5 hover:underline font-mono text-sm"
                >
                  <span className="font-semibold text-slate-200">{String(row.src_lang)}</span>
                  <span className="text-slate-400">→</span>
                  <span className="font-semibold text-slate-200">{String(row.tgt_lang)}</span>
                </Link>
              ),
            },
            {
              key: 'count',
              header: 'count',
              render: (row) => Number(row.count ?? 0).toLocaleString(),
            },
          ]}
        />

        {/* Sample Allocations */}
        <StatTable
          title="By Allocation State"
          subtitle="Sample partitioning across registry roles"
          path="/stats/allocations"
          columns={[
            {
              key: 'allocation',
              header: 'allocation',
              render: (row) => <Badge>{String(row.allocation)}</Badge>,
            },
            {
              key: 'count',
              header: 'count',
              render: (row) => Number(row.count ?? 0).toLocaleString(),
            },
          ]}
        />

        {/* Domains */}
        <StatTable
          title="By Domain"
          subtitle="Top domain categories"
          path="/stats/domains"
          columns={[
            {
              key: 'domain',
              header: 'domain',
              render: (row) => {
                const domainStr = String(row.domain ?? 'unspecified')
                return (
                  <Link
                    to={`/samples?domain=${encodeURIComponent(domainStr)}`}
                    className="hover:underline font-medium text-slate-200"
                  >
                    {domainStr}
                  </Link>
                )
              },
            },
            {
              key: 'count',
              header: 'count',
              render: (row) => Number(row.count ?? 0).toLocaleString(),
            },
          ]}
        />

        {/* Sources */}
        <StatTable
          title="By Provenance Source"
          subtitle="Data origin and provenance breakdown"
          path="/stats/sources"
          columns={[
            {
              key: 'name',
              header: 'Source Name',
              render: (row) => (
                <Link to="/sources" className="hover:underline font-medium text-slate-200">
                  {String(row.name || `Source #${row.source_id}`)}
                </Link>
              ),
            },
            {
              key: 'source_id',
              header: 'source_id',
              render: (row) => (
                <span className="font-mono text-xs text-slate-400">
                  #{String(row.source_id ?? 'N/A')}
                </span>
              ),
            },
            {
              key: 'count',
              header: 'count',
              render: (row) => Number(row.count ?? 0).toLocaleString(),
            },
          ]}
        />

        {/* Ingestion Batches */}
        <StatTable
          title="By Ingestion Batch"
          subtitle="Recent ingestion batches and sample counts"
          path="/stats/batches"
          columns={[
            {
              key: 'name',
              header: 'Batch Name',
              render: (row) => (
                <Link to="/batches" className="hover:underline font-medium text-slate-200">
                  {String(row.name)}
                </Link>
              ),
            },
            {
              key: 'sample_count',
              header: 'sample_count',
              render: (row) => Number(row.sample_count ?? 0).toLocaleString(),
            },
          ]}
        />

        {/* Dataset Definitions */}
        <StatTable
          title="By Dataset Definition"
          subtitle="Registered logical dataset collections"
          path="/stats/datasets"
          columns={[
            {
              key: 'id',
              header: 'id',
              render: (row) => <span className="font-mono text-xs text-slate-400">#{String(row.id)}</span>,
            },
            {
              key: 'name',
              header: 'Dataset Name',
              render: (row) => (
                <Link to="/datasets" className="hover:underline font-medium text-slate-200">
                  {String(row.name)}
                </Link>
              ),
            },
          ]}
        />

        {/* Snapshots */}
        <StatTable
          title="By Exported Snapshot"
          subtitle="Immutable Parquet snapshots for training"
          path="/stats/snapshots"
          columns={[
            {
              key: 'name',
              header: 'Snapshot Name',
              render: (row) => (
                <Link to="/snapshots" className="hover:underline font-medium text-slate-200">
                  {String(row.name)}
                </Link>
              ),
            },
            {
              key: 'total_samples',
              header: 'total_samples',
              render: (row) => Number(row.total_samples ?? 0).toLocaleString(),
            },
          ]}
        />

        {/* Evaluation Sets */}
        <StatTable
          title="By Evaluation Set"
          subtitle="Reserved benchmark collections"
          path="/stats/evaluation-sets"
          columns={[
            {
              key: 'name',
              header: 'Eval Set Name',
              render: (row) => (
                <Link to="/evaluation-sets" className="hover:underline font-medium text-slate-200">
                  {String(row.name)}
                </Link>
              ),
            },
            {
              key: 'sample_count',
              header: 'sample_count',
              render: (row) => Number(row.sample_count ?? 0).toLocaleString(),
            },
          ]}
        />
      </Grid>
    </VStack>
  )
}
