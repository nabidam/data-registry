import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'
import { ProgressBar } from '@astryxdesign/core/ProgressBar'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, ErrorBox, Field, FilePicker, Input, PageHeader, Select } from '@/components/ui'
import { useList } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, ImportStats, ReservationDefaults, Row, Source } from '@/types'

type Inspection = { format: string; columns: string[]; preview: Row[] }
type UploadStart = { batch: Batch; upload_id: string; part_size: number }
type UploadedPart = { part_number: number; etag: string }

const INSPECTION_LIMIT_BYTES = 16 * 1024 * 1024
const ACTIVE_IMPORT_STATUSES = new Set(['uploading', 'queued', 'importing'])
const WORKER_ACTIVE_STATUSES = new Set(['queued', 'importing'])

const POPULAR_LANGUAGES = ['en', 'fa', 'de', 'zh', 'ar', 'es', 'fr', 'ru', 'ja', 'tr']

function formatCount(value: number | undefined) {
  return value === undefined ? '—' : value.toLocaleString()
}

function ImportProgressView({ batch }: { batch: Batch }) {
  const stats = batch.stats as ImportStats | null
  const progress = stats?.progress
  if (!progress) return <Text type="supporting" color="secondary">—</Text>
  const heartbeatAge = progress.updated_at ? Date.now() - new Date(progress.updated_at).getTime() : 0
  const heartbeatStale = batch.status === 'importing' && heartbeatAge > 60_000
  const shards = progress.shards_total
    ? `${progress.shards_processed ?? 0}/${progress.shards_total} shards`
    : progress.shards_processed
      ? `${progress.shards_processed} shards`
      : null
  const stageItems = progress.items_total
    ? `${formatCount(progress.items_processed)}/${formatCount(progress.items_total)}`
    : progress.items_processed !== undefined
      ? formatCount(progress.items_processed)
      : null

  return (
    <VStack gap={1} style={{ minWidth: '16rem' }}>
      <HStack vAlign="center" gap={2}>
        <Text type="body" weight="medium" style={{ textTransform: 'capitalize' }}>
          {progress.phase?.replaceAll('_', ' ') ?? 'Processing'}
        </Text>
        {stats?.attempt && stats.attempt > 1 ? (
          <Badge variant="warning">Attempt {stats.attempt}</Badge>
        ) : null}
      </HStack>
      {progress.stage ? (
        <Text type="supporting" weight="medium">
          {progress.stage.replaceAll('_', ' ')}
          {stageItems ? ` · ${stageItems}` : ''}
          {progress.stage_elapsed_seconds !== undefined
            ? ` · ${progress.stage_elapsed_seconds.toLocaleString()}s`
            : ''}
        </Text>
      ) : null}
      {progress.message && (
        <Text type="supporting" color="secondary">{progress.message}</Text>
      )}
      <Text type="code">
        {formatCount(progress.rows_processed)} rows{shards ? ` · ${shards}` : ''}
      </Text>
      {progress.updated_at ? (
        <Text
          type="supporting"
          style={{ color: heartbeatStale ? '#ef4444' : '#64748b', fontWeight: heartbeatStale ? 600 : 400 }}
        >
          {heartbeatStale ? '⚠️ Worker heartbeat stale · ' : 'Updated '}
          {new Date(progress.updated_at).toLocaleTimeString()}
        </Text>
      ) : null}
    </VStack>
  )
}

export default function Imports() {
  const sources = useList<Source>('sources', { limit: 200 })
  const batches = useList<Batch>('batches', { limit: 15 }, {
    refetchInterval: (query) =>
      query.state.data?.some((batch) => WORKER_ACTIVE_STATUSES.has(batch.status)) ? 2000 : false,
  })
  const qc = useQueryClient()

  // State
  const [activeStep, setActiveStep] = useState<1 | 2 | 3>(1)
  const [inputMode, setInputMode] = useState<'file' | 'uri'>('file')
  const [storageUri, setStorageUri] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [inspection, setInspection] = useState<Inspection | null>(null)
  const [inspecting, setInspecting] = useState(false)
  const [expandedBatchId, setExpandedBatchId] = useState<number | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [retrying, setRetrying] = useState<number | null>(null)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)

  const [form, setForm] = useState({
    batch_name: '',
    src_lang: 'en',
    tgt_lang: 'fa',
    source_id: '',
    domain: '',
    source_column: 'source',
    target_column: 'target',
    domain_column: '',
    quality_column: '',
    document_id_column: '',
    notes: '',
    evaluation_percent: '',
    evaluation_max_samples: '',
    evaluation_selector: '',
    random_seed: '',
  })

  const defaults = useQuery({
    queryKey: ['reservation-defaults'],
    queryFn: () => api.get<ReservationDefaults>('/imports/reservation-defaults'),
  })
  const effectiveSelector = form.evaluation_selector || defaults.data?.evaluation_selector

  async function inspect(f: File) {
    setError(null)
    setInspecting(true)
    const body = new FormData()
    const isPartial = f.size > INSPECTION_LIMIT_BYTES
    const previewFile = isPartial
      ? new File([f.slice(0, INSPECTION_LIMIT_BYTES)], f.name, { type: f.type })
      : f
    body.append('file', previewFile)
    if (isPartial) body.append('partial', 'true')
    try {
      const result = await api.upload<Inspection>('/imports/inspect', body)
      setInspection(result)
      setForm((prev) => ({
        ...prev,
        source_column: result.columns.includes(prev.source_column)
          ? prev.source_column
          : (result.columns[0] ?? ''),
        target_column: result.columns.includes(prev.target_column)
          ? prev.target_column
          : (result.columns[1] ?? ''),
      }))
    } catch (e) {
      setError(e)
    } finally {
      setInspecting(false)
    }
  }

  async function submit(e?: React.FormEvent) {
    if (e) e.preventDefault()
    if (inputMode === 'file' && !file) return
    if (inputMode === 'uri' && !storageUri) return

    setBusy(true)
    setError(null)
    try {
      if (inputMode === 'file' && file) {
        const started = await api.post<UploadStart>('/imports/start', {
          batch_name: form.batch_name,
          filename: file.name,
          size: file.size,
        })
        const parts: UploadedPart[] = []
        const totalParts = Math.ceil(file.size / started.part_size)
        for (let index = 0; index < totalParts; index += 1) {
          const body = new FormData()
          body.append('upload_id', started.upload_id)
          body.append(
            'file',
            new File([file.slice(index * started.part_size, (index + 1) * started.part_size)], file.name, {
              type: file.type,
            }),
          )
          const part = await api.upload<UploadedPart>(
            `/imports/${started.batch.id}/parts/${index + 1}`,
            body,
          )
          parts.push(part)
          setUploadProgress((index + 1) / totalParts)
        }
        const complete = Object.fromEntries(
          Object.entries(form).filter(([, value]) => value !== ''),
        )
        await api.post<Batch>(`/imports/${started.batch.id}/complete`, { ...complete, parts })
      } else {
        // Storage URI ingestion mode
        const complete = Object.fromEntries(
          Object.entries({ ...form, parquet_uri: storageUri }).filter(([, value]) => value !== ''),
        )
        await api.post<Batch>('/imports/uri', complete)
      }

      qc.invalidateQueries({ queryKey: ['batches'] })
      setFile(null)
      setStorageUri('')
      setInspection(null)
      setForm((prev) => ({ ...prev, batch_name: '' }))
      setActiveStep(1)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
      setUploadProgress(null)
    }
  }

  async function retryImport(batchId: number) {
    setRetrying(batchId)
    setError(null)
    try {
      await api.post<Batch>(`/imports/${batchId}/retry`, {})
      await qc.invalidateQueries({ queryKey: ['batches'] })
    } catch (err) {
      setError(err)
    } finally {
      setRetrying(null)
    }
  }

  const isStep1Valid = Boolean(
    form.batch_name.trim() &&
      form.src_lang.trim() &&
      form.tgt_lang.trim() &&
      (inputMode === 'file' ? Boolean(file) : Boolean(storageUri.trim())),
  )

  const columnSelect = (key: keyof typeof form, label: string, optional = false, isSource = false, isTarget = false) => (
    <Field label={label}>
      {inspection ? (
        <VStack gap={1}>
          <Select
            value={form[key]}
            disabled={busy || inspecting}
            onChange={(e) => setForm({ ...form, [key]: e.target.value })}
          >
            {optional && <option value="">— Optional —</option>}
            {inspection.columns.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </Select>
          {(isSource || isTarget) && form[key] && (
            <HStack gap={1} vAlign="center">
              <Badge variant={isSource ? 'success' : 'info'}>
                {isSource ? `Source (${form.src_lang})` : `Target (${form.tgt_lang})`}
              </Badge>
              <Text type="supporting" color="secondary">
                Mapped to column "{form[key]}"
              </Text>
            </HStack>
          )}
        </VStack>
      ) : (
        <Input
          value={form[key]}
          disabled={busy || inspecting}
          placeholder={optional ? 'Optional column name' : undefined}
          onChange={(e) => setForm({ ...form, [key]: e.target.value })}
        />
      )}
    </Field>
  )

  return (
    <VStack gap={4}>
      <PageHeader
        title="Imports"
        subtitle="Each import creates an immutable ingestion batch with reserved evaluation holdout protection"
      />
      <ErrorBox error={error} />
      <ErrorBox error={batches.error} />

      {/* Active Worker Activity Banner */}
      {batches.data?.some((batch) => ACTIVE_IMPORT_STATUSES.has(batch.status)) ? (
        <Card padding={4} style={{ border: '1px solid rgba(59, 130, 246, 0.2)', backgroundColor: 'rgba(59, 130, 246, 0.03)' }}>
          <VStack gap={3}>
            <HStack vAlign="center" hAlign="between">
              <HStack vAlign="center" gap={2}>
                <span className="inline-block w-2.5 h-2.5 rounded-full bg-blue-500 animate-ping" />
                <Heading level={4}>Import Worker Active Telemetry</Heading>
              </HStack>
              <Text type="supporting" color="secondary">Real-time status updates every 2s</Text>
            </HStack>
            <VStack gap={3}>
              {batches.data
                .filter((batch) => ACTIVE_IMPORT_STATUSES.has(batch.status))
                .map((batch) => (
                  <Card key={batch.id} padding={3}>
                    <HStack vAlign="start" hAlign="between" gap={3}>
                      <VStack gap={1}>
                        <HStack vAlign="center" gap={2}>
                          <Text type="body" weight="medium">{batch.name}</Text>
                          <Badge>{batch.status}</Badge>
                        </HStack>
                        <Text type="supporting" color="secondary">
                          Batch #{batch.id} · Format: {batch.format ?? 'Unknown'}
                        </Text>
                      </VStack>
                      <ImportProgressView batch={batch} />
                    </HStack>
                  </Card>
                ))}
            </VStack>
          </VStack>
        </Card>
      ) : null}

      {/* Main Multi-Step Ingestion Form */}
      <Card padding={5}>
        <form onSubmit={submit}>
          <VStack gap={4}>

            {/* Stepper Header */}
            <HStack vAlign="center" hAlign="between" style={{ borderBottom: '1px solid var(--border-color, #e2e8f0)', paddingBottom: '1rem' }}>
              <HStack gap={2} vAlign="center">
                <Button
                  type="button"
                  variant={activeStep === 1 ? 'primary' : 'ghost'}
                  disabled={busy}
                  onClick={() => setActiveStep(1)}
                >
                  1. Source & Languages
                </Button>
                <Text type="supporting" color="secondary">→</Text>
                <Button
                  type="button"
                  variant={activeStep === 2 ? 'primary' : 'ghost'}
                  disabled={busy || !isStep1Valid}
                  onClick={() => isStep1Valid && setActiveStep(2)}
                >
                  2. Schema & Live Preview
                </Button>
                <Text type="supporting" color="secondary">→</Text>
                <Button
                  type="button"
                  variant={activeStep === 3 ? 'primary' : 'ghost'}
                  disabled={busy || !isStep1Valid}
                  onClick={() => isStep1Valid && setActiveStep(3)}
                >
                  3. Evaluation Holdout Policy
                </Button>
              </HStack>

              <Text type="supporting" color="secondary">
                Step {activeStep} of 3
              </Text>
            </HStack>

            {/* STEP 1: Source & Languages */}
            {activeStep === 1 && (
              <VStack gap={4}>
                {/* Input Mode Selector */}
                <VStack gap={2}>
                  <Text type="body" weight="medium">Ingestion Mechanism</Text>
                  <HStack gap={2}>
                    <Button
                      type="button"
                      variant={inputMode === 'file' ? 'primary' : 'ghost'}
                      disabled={busy}
                      onClick={() => setInputMode('file')}
                    >
                      📁 Local File Upload (CSV, TSV, Parquet, JSON, TMX)
                    </Button>
                    <Button
                      type="button"
                      variant={inputMode === 'uri' ? 'primary' : 'ghost'}
                      disabled={busy}
                      onClick={() => setInputMode('uri')}
                    >
                      ☁️ Object Storage URI (S3 / MinIO / GCS)
                    </Button>
                  </HStack>
                </VStack>

                <Grid columns={2} gap={4}>
                  {inputMode === 'file' ? (
                    <VStack gap={2}>
                      <FilePicker
                        label="Dataset File *"
                        required
                        accept=".csv,.tsv,.parquet,.json,.jsonl,.tmx,.xlsx"
                        placeholder="Select dataset file to inspect & upload"
                        onChange={(f) => {
                          setFile(f)
                          if (f) {
                            if (!form.batch_name) {
                              const cleanName = f.name.replace(/\.[^/.]+$/, '').replace(/[^a-zA-Z0-9_-]/g, ' ')
                              setForm((prev) => ({ ...prev, batch_name: cleanName }))
                            }
                            void inspect(f)
                          }
                        }}
                      />
                      {inspecting && (
                        <Card padding={2} style={{ backgroundColor: 'rgba(59, 130, 246, 0.05)' }}>
                          <HStack vAlign="center" gap={2} aria-live="polite">
                            <span className="inline-block w-4 h-4 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
                            <Text type="supporting" weight="medium">
                              Parsing header columns and inspecting sample rows…
                            </Text>
                          </HStack>
                        </Card>
                      )}
                    </VStack>
                  ) : (
                    <Field label="Object Storage Parquet/TSV URI *">
                      <Input
                        required
                        value={storageUri}
                        disabled={busy}
                        placeholder="s3://datasets/mt/2026/wmt_en_fa.parquet or minio://raw/batch_01.tsv"
                        onChange={(e) => setStorageUri(e.target.value)}
                      />
                    </Field>
                  )}

                  <Field label="Batch Name *">
                    <Input
                      required
                      value={form.batch_name}
                      disabled={busy}
                      placeholder="e.g. WMT 2026 English-Persian Ingestion"
                      onChange={(e) => setForm({ ...form, batch_name: e.target.value })}
                    />
                  </Field>
                </Grid>

                {/* Language Selection */}
                <VStack gap={2}>
                  <Grid columns={2} gap={4}>
                    <Field label="Source Language (ISO code) *">
                      <Input
                        required
                        value={form.src_lang}
                        disabled={busy}
                        placeholder="e.g. en"
                        onChange={(e) => setForm({ ...form, src_lang: e.target.value })}
                      />
                    </Field>
                    <Field label="Target Language (ISO code) *">
                      <Input
                        required
                        value={form.tgt_lang}
                        disabled={busy}
                        placeholder="e.g. fa"
                        onChange={(e) => setForm({ ...form, tgt_lang: e.target.value })}
                      />
                    </Field>
                  </Grid>

                  {/* Quick-fill language chips */}
                  <HStack gap={1} vAlign="center" wrap="wrap">
                    <Text type="supporting" color="secondary">Quick pick source:</Text>
                    {POPULAR_LANGUAGES.map((lang) => (
                      <Button
                        key={`src-${lang}`}
                        type="button"
                        variant="ghost"
                        disabled={busy}
                        onClick={() => setForm({ ...form, src_lang: lang })}
                      >
                        {lang}
                      </Button>
                    ))}
                  </HStack>
                </VStack>

                {form.src_lang && form.tgt_lang && form.src_lang.trim() === form.tgt_lang.trim() && (
                  <Card padding={2} style={{ backgroundColor: 'rgba(245, 158, 11, 0.1)', borderColor: '#f59e0b' }}>
                    <Text type="supporting" style={{ color: '#b45309', fontWeight: 600 }}>
                      ⚠️ Source and Target language codes are identical ({form.src_lang}). Please confirm if this is a monolingual corpus.
                    </Text>
                  </Card>
                )}

                {/* Optional Metadata */}
                <Grid columns={3} gap={4}>
                  <Field label="Source / Provenance Origin">
                    <Select
                      value={form.source_id}
                      disabled={busy}
                      onChange={(e) => setForm({ ...form, source_id: e.target.value })}
                    >
                      <option value="">— Unassigned —</option>
                      {sources.data?.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name} ({s.kind})
                        </option>
                      ))}
                    </Select>
                  </Field>

                  <Field label="Default Domain">
                    <Input
                      value={form.domain}
                      disabled={busy}
                      placeholder="e.g. news, medical, legal, general"
                      onChange={(e) => setForm({ ...form, domain: e.target.value })}
                    />
                  </Field>

                  <Field label="Notes / Ingestion Context">
                    <Input
                      value={form.notes}
                      disabled={busy}
                      placeholder="Internal tracking notes"
                      onChange={(e) => setForm({ ...form, notes: e.target.value })}
                    />
                  </Field>
                </Grid>
              </VStack>
            )}

            {/* STEP 2: Schema Mapping & Live Preview */}
            {activeStep === 2 && (
              <VStack gap={4}>
                <VStack gap={1}>
                  <Heading level={3}>Column Mapping & Live Sample Preview</Heading>
                  <Text type="supporting" color="secondary">
                    {inspection
                      ? `Detected file format: ${inspection.format}. Assign dataset fields to file header columns.`
                      : 'Assign schema column names below to match your dataset structure.'}
                  </Text>
                </VStack>

                <Grid columns={3} gap={4}>
                  {columnSelect('source_column', 'Source Text Column *', false, true, false)}
                  {columnSelect('target_column', 'Target Text Column *', false, false, true)}
                  {columnSelect('domain_column', 'Domain Column', true)}
                  {columnSelect('quality_column', 'Quality Score Column', true)}
                  {columnSelect('document_id_column', 'Document ID Column', true)}
                </Grid>

                {/* Integrated Live Inspection Data Table */}
                {inspection ? (
                  <Card padding={4} style={{ backgroundColor: 'var(--card-bg, #f8fafc)', border: '1px solid var(--border-color, #cbd5e1)' }}>
                    <VStack gap={3}>
                      <HStack vAlign="center" hAlign="between">
                        <HStack vAlign="center" gap={2}>
                          <Text type="body" weight="medium">Live Parsed Inspection Preview</Text>
                          <Badge variant="info">{inspection.format.toUpperCase()}</Badge>
                        </HStack>
                        <Text type="supporting" color="secondary">
                          Showing first {inspection.preview.length} sample rows
                        </Text>
                      </HStack>

                      <DataTable
                        rows={inspection.preview}
                        columns={inspection.columns.map((c) => ({
                          key: c,
                          header: c === form.source_column
                            ? `${c} (SRC: ${form.src_lang})`
                            : c === form.target_column
                              ? `${c} (TGT: ${form.tgt_lang})`
                              : c,
                        }))}
                      />
                    </VStack>
                  </Card>
                ) : (
                  <Card padding={4}>
                    <Text type="supporting" color="secondary" style={{ textAlign: 'center' }}>
                      No live preview available for Storage URI mode. Proceed with column mapping.
                    </Text>
                  </Card>
                )}
              </VStack>
            )}

            {/* STEP 3: Evaluation Holdout Policy */}
            {activeStep === 3 && (
              <VStack gap={4}>
                <VStack gap={1}>
                  <Heading level={3}>Evaluation Holdout Reservation Policy</Heading>
                  <Text type="supporting" color="secondary">
                    Before any sample becomes trainable, a slice is automatically reserved for evaluation sets and benchmarks.
                  </Text>
                </VStack>

                <Card padding={4} style={{ backgroundColor: 'rgba(59, 130, 246, 0.05)', borderColor: 'rgba(59, 130, 246, 0.3)' }}>
                  <VStack gap={2}>
                    <HStack vAlign="center" gap={2}>
                      <Badge variant="info">Policy: {effectiveSelector ?? 'contamination_safe'}</Badge>
                      <Text type="body" weight="medium">
                        {defaults.data?.policies.contamination_safe.description ?? 'Contamination-Safe Document Holdout'}
                      </Text>
                    </HStack>
                    <Text type="supporting" color="secondary">
                      {defaults.data?.policies.contamination_safe.contamination.document_level_holdout === true
                        ? 'Document-level holdout is active. Near-duplicate sentences are quarantined to protect evaluation benchmarks.'
                        : 'Document-level holdout is disabled.'}
                    </Text>
                  </VStack>
                </Card>

                <Grid columns={4} gap={4}>
                  <Field label={`Holdout % (Default ${defaults.data?.evaluation_percent ?? '5'})`}>
                    <Input
                      type="number"
                      step="0.1"
                      disabled={busy}
                      placeholder={String(defaults.data?.evaluation_percent ?? '5')}
                      value={form.evaluation_percent}
                      onChange={(e) => setForm({ ...form, evaluation_percent: e.target.value })}
                    />
                  </Field>

                  <Field label={`Max Reserved Samples (Default ${defaults.data?.evaluation_max_samples ?? '5000'})`}>
                    <Input
                      type="number"
                      disabled={busy}
                      placeholder={String(defaults.data?.evaluation_max_samples ?? '5000')}
                      value={form.evaluation_max_samples}
                      onChange={(e) => setForm({ ...form, evaluation_max_samples: e.target.value })}
                    />
                  </Field>

                  <Field label="Reservation Selector">
                    <Select
                      value={form.evaluation_selector}
                      disabled={busy}
                      onChange={(e) => setForm({ ...form, evaluation_selector: e.target.value })}
                    >
                      <option value="">{defaults.data?.evaluation_selector ?? 'default'}</option>
                      {defaults.data?.selectors
                        .filter((name) => name !== defaults.data?.evaluation_selector)
                        .map((name) => (
                          <option key={name} value={name}>
                            {name}
                          </option>
                        ))}
                    </Select>
                  </Field>

                  <Field label={`Random Seed (Default ${defaults.data?.random_seed ?? '42'})`}>
                    <Input
                      type="number"
                      disabled={busy}
                      placeholder={String(defaults.data?.random_seed ?? '42')}
                      value={form.random_seed}
                      onChange={(e) => setForm({ ...form, random_seed: e.target.value })}
                    />
                  </Field>
                </Grid>

                {/* Pre-Flight Summary Card */}
                <Card padding={4} style={{ border: '1px solid var(--border-color, #cbd5e1)' }}>
                  <VStack gap={2}>
                    <Heading level={4}>Ingestion Pre-Flight Confirmation</Heading>
                    <Grid columns={3} gap={3}>
                      <VStack gap={1}>
                        <Text type="supporting" color="secondary">Batch Name</Text>
                        <Text type="body" weight="medium">{form.batch_name}</Text>
                      </VStack>
                      <VStack gap={1}>
                        <Text type="supporting" color="secondary">Language Pair</Text>
                        <Text type="body" weight="medium">{form.src_lang} → {form.tgt_lang}</Text>
                      </VStack>
                      <VStack gap={1}>
                        <Text type="supporting" color="secondary">Column Mapping</Text>
                        <Text type="body" weight="medium">
                          {form.source_column} / {form.target_column}
                        </Text>
                      </VStack>
                    </Grid>
                  </VStack>
                </Card>

                {uploadProgress !== null && (
                  <VStack gap={2}>
                    <ProgressBar value={uploadProgress * 100} label="Uploading file chunks to MinIO/S3" />
                    <Text type="supporting" color="secondary" style={{ textAlign: 'center' }}>
                      Do not close window until upload completes.
                    </Text>
                  </VStack>
                )}
              </VStack>
            )}

            {/* Stepper Navigation Footer */}
            <HStack vAlign="center" hAlign="between" style={{ borderTop: '1px solid var(--border-color, #e2e8f0)', paddingTop: '1rem' }}>
              <Button
                type="button"
                variant="ghost"
                disabled={busy || activeStep === 1}
                onClick={() => setActiveStep((prev) => Math.max(1, prev - 1) as 1 | 2 | 3)}
              >
                ← Back
              </Button>

              {activeStep < 3 ? (
                <Button
                  type="button"
                  variant="primary"
                  disabled={busy || !isStep1Valid}
                  onClick={() => isStep1Valid && setActiveStep((prev) => Math.min(3, prev + 1) as 1 | 2 | 3)}
                >
                  Next: {activeStep === 1 ? 'Schema & Preview' : 'Evaluation Policy'} →
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="primary"
                  disabled={busy || !isStep1Valid}
                >
                  {uploadProgress === null
                    ? busy
                      ? 'Preparing Import Batch…'
                      : '🚀 Launch Ingestion Batch'
                    : `Uploading ${Math.round(uploadProgress * 100)}%`}
                </Button>
              )}
            </HStack>

          </VStack>
        </form>
      </Card>

      {/* Streamlined Recent Batches Table & Expandable Worker Drawer */}
      <VStack gap={3}>
        <HStack vAlign="center" hAlign="between">
          <VStack gap={1}>
            <Heading level={3}>Recent Ingestion Batches</Heading>
            <Text type="supporting" color="secondary">
              Click any batch row to expand full worker progress telemetry and error diagnostic logs
            </Text>
          </VStack>
          <Badge variant="neutral">{batches.data?.length ?? 0} Total Batches</Badge>
        </HStack>

        <DataTable
          loading={batches.isLoading}
          rows={batches.data as unknown as Row[]}
          columns={[
            { key: 'id', header: 'ID', width: '70px' },
            {
              key: 'name',
              header: 'Batch Name',
              render: (r) => (
                <VStack gap={0}>
                  <Text type="body" weight="medium">{String(r.name)}</Text>
                  {r.notes ? <Text type="supporting" color="secondary">{String(r.notes)}</Text> : null}
                </VStack>
              ),
            },
            { key: 'format', header: 'Format', render: (r) => String(r.format ?? 'parquet') },
            {
              key: 'sample_count',
              header: 'Samples',
              render: (r) => formatCount(Number(r.sample_count)),
            },
            {
              key: 'reserved',
              header: 'Reserved Eval',
              render: (r) => {
                const reserved = (r.stats as { reservation?: { reserved?: number } })?.reservation?.reserved
                return reserved !== undefined ? formatCount(reserved) : '—'
              },
            },
            {
              key: 'status',
              header: 'Status',
              render: (r) => <Badge>{String(r.status)}</Badge>,
            },
            {
              key: 'actions',
              header: 'Telemetry & Actions',
              render: (r) => {
                const isExpanded = expandedBatchId === Number(r.id)
                return (
                  <HStack gap={2} vAlign="center">
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => setExpandedBatchId(isExpanded ? null : Number(r.id))}
                    >
                      {isExpanded ? 'Hide Telemetry ▲' : 'Inspect Telemetry ▼'}
                    </Button>
                    {r.status === 'failed' && (
                      <Button
                        type="button"
                        variant="ghost"
                        disabled={retrying === Number(r.id)}
                        onClick={() => void retryImport(Number(r.id))}
                      >
                        {retrying === Number(r.id) ? 'Retrying…' : 'Retry'}
                      </Button>
                    )}
                  </HStack>
                )
              },
            },
          ]}
        />

        {/* Expanded Row Detail Drawer */}
        {expandedBatchId !== null && (() => {
          const selectedBatch = batches.data?.find((b) => b.id === expandedBatchId)
          if (!selectedBatch) return null
          const stats = selectedBatch.stats as ImportStats | null
          const res = (stats?.reservation as {
            reserved?: number
            quarantined?: number
            selection?: { splits?: Record<string, number>; human_verify?: number }
          })

          return (
            <Card padding={4} style={{ backgroundColor: 'var(--card-bg, #f8fafc)', border: '1px solid #3b82f6' }}>
              <VStack gap={3}>
                <HStack vAlign="center" hAlign="between">
                  <HStack vAlign="center" gap={2}>
                    <Heading level={4}>Batch #{selectedBatch.id} Worker Telemetry & Reservation Breakdown</Heading>
                    <Badge>{selectedBatch.status}</Badge>
                  </HStack>
                  <Button type="button" variant="ghost" onClick={() => setExpandedBatchId(null)}>
                    ✕ Close Drawer
                  </Button>
                </HStack>

                <Grid columns={4} gap={3}>
                  <Card padding={3}>
                    <VStack gap={1}>
                      <Text type="supporting" color="secondary">Total Samples</Text>
                      <Text type="body" weight="medium">{formatCount(selectedBatch.sample_count)}</Text>
                    </VStack>
                  </Card>
                  <Card padding={3}>
                    <VStack gap={1}>
                      <Text type="supporting" color="secondary">Reserved Evaluation</Text>
                      <Text type="body" weight="medium">{formatCount(res?.reserved)}</Text>
                    </VStack>
                  </Card>
                  <Card padding={3}>
                    <VStack gap={1}>
                      <Text type="supporting" color="secondary">Quarantined Holdout</Text>
                      <Text type="body" weight="medium">{formatCount(res?.quarantined ?? 0)}</Text>
                    </VStack>
                  </Card>
                  <Card padding={3}>
                    <VStack gap={1}>
                      <Text type="supporting" color="secondary">Dev / Test Splits</Text>
                      <Text type="body" weight="medium">
                        {res?.selection?.splits
                          ? `${res.selection.splits.dev ?? 0} dev / ${res.selection.splits.test ?? 0} test`
                          : '—'}
                      </Text>
                    </VStack>
                  </Card>
                </Grid>

                {stats?.error && (
                  <Card padding={3} style={{ backgroundColor: 'rgba(239, 68, 68, 0.05)', borderColor: '#ef4444' }}>
                    <VStack gap={1}>
                      <Text type="body" weight="medium" style={{ color: '#ef4444' }}>Worker Ingestion Failure Stack Trace</Text>
                      <Text type="code" style={{ color: '#b91c1c', whiteSpace: 'pre-wrap' }}>
                        {String(stats.error)}
                      </Text>
                    </VStack>
                  </Card>
                )}

                <VStack gap={2}>
                  <Text type="supporting" color="secondary" weight="medium">Detailed Progress Metric:</Text>
                  <ImportProgressView batch={selectedBatch} />
                </VStack>
              </VStack>
            </Card>
          )
        })()}
      </VStack>
    </VStack>
  )
}
