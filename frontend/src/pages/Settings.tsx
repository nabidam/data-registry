import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Text, Heading } from '@astryxdesign/core/Text'
import { Spinner } from '@astryxdesign/core/Spinner'

import { Card, PageHeader, Badge, Button, ErrorBox } from '@/components/ui'
import { api } from '@/lib/api'

interface SettingsData {
  app_name?: string | null
  storage_backend?: string | null
  bucket?: string | null
  storage_root?: string | null
  s3_endpoint_url?: string | null
  mlflow_tracking_uri?: string | null
  work_dir?: string | null
  evaluation_percent?: number | string | null
  evaluation_max_samples?: number | string | null
  evaluation_selector?: string | null
  random_seed?: number | string | null
  import_stream_threshold_mb?: number | string | null
  import_batch_rows?: number | string | null
  evaluation_candidate_limit?: number | string | null
  evaluation_candidate_multiplier?: number | string | null
  [key: string]: unknown
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Fallback if clipboard API is restricted
    }
  }

  return (
    <button
      type="button"
      onClick={handleCopy}
      title="Copy to clipboard"
      className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded border border-slate-700 bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white transition-colors cursor-pointer"
    >
      {copied ? (
        <>
          <svg className="w-3 h-3 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
          </svg>
          <span className="text-emerald-400 font-medium">Copied</span>
        </>
      ) : (
        <>
          <svg className="w-3 h-3 text-slate-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
          </svg>
          <span>Copy</span>
        </>
      )}
    </button>
  )
}

function SettingRow({
  label,
  value,
  description,
  badge,
  isCopyable = false,
  isUrl = false,
}: {
  label: string
  value: string | number | null | undefined
  description?: string
  badge?: React.ReactNode
  isCopyable?: boolean
  isUrl?: boolean
}) {
  const displayVal = value !== null && value !== undefined && value !== '' ? String(value) : '—'

  return (
    <div className="py-2.5 border-b border-slate-800/60 last:border-b-0 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
      <VStack gap={0} style={{ flex: '1 1 50%' }}>
        <Text type="supporting" weight="medium" style={{ color: 'var(--color-text-main, #f1f5f9)' }}>
          {label}
        </Text>
        {description && (
          <Text type="supporting" color="secondary" style={{ fontSize: '0.75rem' }}>
            {description}
          </Text>
        )}
      </VStack>
      <HStack gap={2} vAlign="center" style={{ flexShrink: 0 }}>
        {badge}
        {isUrl && displayVal !== '—' ? (
          <a
            href={displayVal}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 font-mono text-xs text-blue-400 hover:text-blue-300 underline break-all"
          >
            {displayVal}
            <svg className="w-3 h-3 inline-block shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
            </svg>
          </a>
        ) : (
          <Text type="code" style={{ wordBreak: 'break-all', fontSize: '0.85rem' }}>
            {displayVal}
          </Text>
        )}
        {isCopyable && displayVal !== '—' && <CopyButton value={displayVal} />}
      </HStack>
    </div>
  )
}

function SectionHeader({ icon, title, subtitle }: { icon: string; title: string; subtitle?: string }) {
  return (
    <div className="pb-3 mb-3 border-b border-slate-800 flex items-center gap-2">
      <span className="text-xl">{icon}</span>
      <VStack gap={0}>
        <Heading level={3}>{title}</Heading>
        {subtitle && (
          <Text type="supporting" color="secondary" style={{ fontSize: '0.75rem' }}>
            {subtitle}
          </Text>
        )}
      </VStack>
    </div>
  )
}

export default function Settings() {
  const { data, isLoading, isError, error, refetch } = useQuery<SettingsData>({
    queryKey: ['settings'],
    queryFn: () => api.get<SettingsData>('/settings'),
  })

  const formatNumber = (val: number | string | null | undefined, suffix = '') => {
    if (val === null || val === undefined) return '—'
    const num = Number(val)
    if (isNaN(num)) return String(val)
    return `${num.toLocaleString()}${suffix}`
  }

  const formatPercent = (val: number | string | null | undefined) => {
    if (val === null || val === undefined) return '—'
    const num = Number(val)
    if (isNaN(num)) return String(val)
    return `${(num * 100).toFixed(1)}% (${num})`
  }

  const storageBackend = data?.storage_backend ?? 'local'
  const isS3 = storageBackend.toLowerCase().includes('s3') || storageBackend.toLowerCase().includes('minio')

  return (
    <VStack gap={5}>
      <PageHeader
        title="Deployment Settings"
        subtitle="Read-only environment configurations powering data ingestion, Parquet builds, and evaluation benchmarks."
        actions={
          <HStack gap={2} vAlign="center">
            <Badge variant="success">Environment Sealed</Badge>
            <Button variant="ghost" label="Refresh" onClick={() => refetch()} />
          </HStack>
        }
      />

      {isError && (
        <VStack gap={3}>
          <ErrorBox error={error} />
          <HStack hAlign="end">
            <Button variant="primary" label="Retry Connection" onClick={() => refetch()} />
          </HStack>
        </VStack>
      )}

      {isLoading ? (
        <Card>
          <VStack gap={4} hAlign="center" vAlign="center" style={{ padding: '3rem 0' }}>
            <Spinner size="md" />
            <Text type="supporting" color="secondary">
              Resolving active deployment parameters...
            </Text>
          </VStack>
        </Card>
      ) : data ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Section 1: Storage & Object Store */}
          <Card>
            <SectionHeader
              icon="📦"
              title="Storage & Object Store"
              subtitle="Parquet batch store and raw storage backend engine"
            />
            <VStack gap={1}>
              <SettingRow
                label="Storage Engine"
                value={storageBackend.toUpperCase()}
                description="Active immutable data store driver"
                badge={
                  <Badge variant={isS3 ? 'success' : 'info'}>
                    {isS3 ? 'Cloud Object Store' : 'Local Parquet Store'}
                  </Badge>
                }
              />
              <SettingRow
                label="Storage Root Path"
                value={data.storage_root}
                description="Root directory on host filesystem"
                isCopyable
              />
              <SettingRow
                label="S3 / MinIO Bucket"
                value={data.bucket}
                description="Target cloud storage bucket"
                isCopyable
              />
              <SettingRow
                label="S3 Endpoint URL"
                value={data.s3_endpoint_url}
                description="MinIO or AWS S3 API service URL"
                isUrl
                isCopyable
              />
              <SettingRow
                label="Scratch Work Directory"
                value={data.work_dir}
                description="Temporary directory for DuckDB operations"
                isCopyable
              />
            </VStack>
          </Card>

          {/* Section 2: Evaluation Pipeline Defaults */}
          <Card>
            <SectionHeader
              icon="📊"
              title="Evaluation Benchmark Pipeline"
              subtitle="Automatic slice reservation and test set selection rules"
            />
            <VStack gap={1}>
              <SettingRow
                label="Evaluation Reserve Ratio"
                value={formatPercent(data.evaluation_percent)}
                description="Slice of batch automatically reserved for benchmark sets"
              />
              <SettingRow
                label="Max Reserved Samples"
                value={formatNumber(data.evaluation_max_samples, ' samples')}
                description="Upper bound cap on reserved evaluation pairs per batch"
              />
              <SettingRow
                label="Selector Strategy"
                value={data.evaluation_selector}
                description="Sampling logic used for benchmark slice selection"
                badge={<Badge variant="neutral">Deterministic</Badge>}
              />
              <SettingRow
                label="Default Random Seed"
                value={data.random_seed}
                description="Reproducibility seed for dataset split generation"
                isCopyable
              />
              <SettingRow
                label="Candidate Search Limit"
                value={formatNumber(data.evaluation_candidate_limit, ' candidates')}
                description="Maximum pool size scanned for benchmark matching"
              />
              <SettingRow
                label="Candidate Multiplier"
                value={data.evaluation_candidate_multiplier ? `${data.evaluation_candidate_multiplier}x` : '—'}
                description="Oversampling ratio during evaluation candidate selection"
              />
            </VStack>
          </Card>

          {/* Section 3: Ingestion Engine */}
          <Card>
            <SectionHeader
              icon="⚡"
              title="Ingestion Engine & Streaming"
              subtitle="Raw dataset ingestion limits and DuckDB streaming thresholds"
            />
            <VStack gap={1}>
              <SettingRow
                label="System Registry Name"
                value={data.app_name}
                description="Target deployment identifier"
              />
              <SettingRow
                label="Stream Threshold"
                value={formatNumber(data.import_stream_threshold_mb, ' MB')}
                description="Files larger than this threshold use chunked streaming"
              />
              <SettingRow
                label="Batch Processing Rows"
                value={formatNumber(data.import_batch_rows, ' rows / batch')}
                description="DuckDB micro-batch chunk size during conversion"
              />
            </VStack>
          </Card>

          {/* Section 4: MLflow & Experiment Tracking */}
          <Card>
            <SectionHeader
              icon="🧪"
              title="MLflow Experiment Tracking"
              subtitle="Model checkpoint logging and external MLflow server"
            />
            <VStack gap={1}>
              <SettingRow
                label="MLflow Tracking Server"
                value={data.mlflow_tracking_uri}
                description="Remote tracking server URL for experiment run metrics"
                isUrl
                isCopyable
                badge={
                  <Badge variant={data.mlflow_tracking_uri ? 'success' : 'warning'}>
                    {data.mlflow_tracking_uri ? 'Configured' : 'Not Configured'}
                  </Badge>
                }
              />
            </VStack>
          </Card>
        </div>
      ) : null}
    </VStack>
  )
}

