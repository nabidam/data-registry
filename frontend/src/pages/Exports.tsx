import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'

import { Badge, Button, Card, Checkbox, PageHeader, RadioGroup, RadioItem, Select } from '@/components/ui'
import { DataTable } from '@/components/DataTable'
import { api } from '@/lib/api'
import type { EvaluationSet, Row } from '@/types'

type SnapshotExport = {
  snapshot_id: number
  name: string
  prefix_uri: string | null
  counts: Record<string, number>
  created_at: string
}

type ExportFormat = 'parquet' | 'csv' | 'tsv' | 'jsonl' | 'huggingface'
type ExportSource = 'snapshot' | 'evaluation-set'

const commonColumns = [
  { id: 'source_text', label: 'Source text', group: 'Translation' },
  { id: 'target_text', label: 'Target text', group: 'Translation' },
  { id: 'src_lang', label: 'Source language', group: 'Language' },
  { id: 'tgt_lang', label: 'Target language', group: 'Language' },
  { id: 'sample_id', label: 'Sample ID', group: 'Provenance' },
  { id: 'batch_id', label: 'Batch ID', group: 'Provenance' },
  { id: 'source_id', label: 'Source ID', group: 'Provenance' },
  { id: 'domain', label: 'Domain', group: 'Metadata' },
  { id: 'quality', label: 'Quality', group: 'Metadata' },
  { id: 'document_id', label: 'Document ID', group: 'Metadata' },
  { id: 'meta', label: 'Imported metadata', group: 'Metadata' },
] as const

const evaluationColumns = [
  { id: 'evaluation_split', label: 'Evaluation split', group: 'Evaluation' },
  { id: 'human_verify', label: 'Human verification', group: 'Evaluation' },
  { id: 'n_tokens', label: 'Token count', group: 'Selection signals' },
  { id: 'length_bucket', label: 'Length bucket', group: 'Selection signals' },
  { id: 'has_math', label: 'Math', group: 'Selection signals' },
  { id: 'has_numbers_units', label: 'Numbers and units', group: 'Selection signals' },
  { id: 'has_acronyms', label: 'Acronyms', group: 'Selection signals' },
  { id: 'has_mixed_script', label: 'Mixed script', group: 'Selection signals' },
  { id: 'is_rare_term', label: 'Rare term', group: 'Selection signals' },
  { id: 'rare_term_score', label: 'Rare-term score', group: 'Selection signals' },
] as const

const translationColumns = ['source_text', 'target_text', 'src_lang', 'tgt_lang']
const splitOptions = ['train', 'validation', 'test'] as const

const formatOptions: Array<{ value: ExportFormat; label: string; detail: string }> = [
  { value: 'parquet', label: 'Parquet', detail: 'Columnar, compressed' },
  { value: 'jsonl', label: 'JSONL', detail: 'One record per line' },
  { value: 'csv', label: 'CSV', detail: 'Spreadsheet-friendly' },
  { value: 'tsv', label: 'TSV', detail: 'Tab-separated text' },
  { value: 'huggingface', label: 'Hugging Face dataset', detail: 'Loadable DatasetDict package' },
]

export default function Exports() {
  const { data: snapshots, isLoading: snapshotsLoading } = useQuery({
    queryKey: ['exports'],
    queryFn: () => api.get<SnapshotExport[]>('/exports'),
  })
  const { data: evaluationSets, isLoading: evaluationSetsLoading } = useQuery({
    queryKey: ['evaluation-sets', 'exports'],
    queryFn: () => api.get<EvaluationSet[]>('/evaluation-sets'),
  })
  const [source, setSource] = useState<ExportSource>('snapshot')
  const [snapshotId, setSnapshotId] = useState('')
  const [evaluationSetId, setEvaluationSetId] = useState('')
  const [format, setFormat] = useState<ExportFormat>('parquet')
  const [selectedColumns, setSelectedColumns] = useState<string[]>(translationColumns)
  const [selectedSplits, setSelectedSplits] = useState<string[]>([...splitOptions])
  const [includeManifest, setIncludeManifest] = useState(true)

  const activeSnapshotId = snapshotId || (snapshots?.[0] ? String(snapshots[0].snapshot_id) : '')
  const activeEvaluationSetId =
    evaluationSetId || (evaluationSets?.[0] ? String(evaluationSets[0].id) : '')
  const availableColumns = source === 'evaluation-set'
    ? [...commonColumns, ...evaluationColumns]
    : [...commonColumns]
  const allColumns = availableColumns.map((column) => column.id)
  const groupedColumns = useMemo(
    () => [...new Set(availableColumns.map((column) => column.group))].map((group) => ({
      group,
      columns: availableColumns.filter((column) => column.group === group),
    })),
    [source],
  )
  const isHuggingFace = format === 'huggingface'
  const effectiveSplits = source === 'snapshot'
    ? (isHuggingFace ? [...splitOptions] : selectedSplits)
    : []
  const selectedAvailableColumns = selectedColumns.filter((column) =>
    allColumns.includes(column as typeof allColumns[number]),
  )
  const customUrl = (() => {
    if (!selectedAvailableColumns.length) return undefined
    if (source === 'evaluation-set') {
      if (!activeEvaluationSetId) return undefined
      return api.downloadUrl(
        `/exports/evaluation-sets/${activeEvaluationSetId}/custom?${new URLSearchParams({
          format,
          columns: selectedAvailableColumns.join(','),
          include_metadata: String(isHuggingFace || includeManifest),
        }).toString()}`,
      )
    }
    if (!activeSnapshotId || !effectiveSplits.length) return undefined
    return api.downloadUrl(
      `/exports/${activeSnapshotId}/custom?${new URLSearchParams({
        format,
        columns: selectedAvailableColumns.join(','),
        splits: effectiveSplits.join(','),
        include_manifest: String(isHuggingFace || includeManifest),
      }).toString()}`,
    )
  })()

  const toggle = (value: string, selected: string[], setSelected: (values: string[]) => void) => {
    setSelected(selected.includes(value) ? selected.filter((item) => item !== value) : [...selected, value])
  }
  const directLink = (id: number, file: string) => (
    <a
      key={file}
      className="mr-3 text-sm text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-slate-950"
      href={api.downloadUrl(`/exports/${id}/${file}`)}
    >
      {file}
    </a>
  )

  return (
    <VStack gap={4}>
      <PageHeader
        title="Exports"
        subtitle="Download immutable snapshots and evaluation sets as-is, or prepare a purpose-built research package."
      />

      <Card>
        <VStack gap={4}>
          <HStack vAlign="center" hAlign="between">
            <VStack gap={1}>
              <Heading level={4}>Custom export</Heading>
              <Text type="supporting" color="secondary">
                Your choices create a download only; the immutable source remains unchanged.
              </Text>
            </VStack>
            <Badge variant="neutral">
              {selectedAvailableColumns.length} columns
              {source === 'snapshot' ? ` · ${effectiveSplits.length} splits` : ' · evaluation set'}
            </Badge>
          </HStack>

          <Grid columns={2} gap={4}>
            <VStack gap={3}>
              <VStack gap={1}>
                <Text type="label" weight="medium">Source type</Text>
                <Select
                  value={source}
                  onChange={(event) => setSource(event.target.value as ExportSource)}
                >
                  <option value="snapshot">Training snapshot</option>
                  <option value="evaluation-set">Evaluation set</option>
                </Select>
              </VStack>

              <VStack gap={1}>
                <Text type="label" weight="medium">
                  {source === 'snapshot' ? 'Snapshot' : 'Evaluation set'}
                </Text>
                {source === 'snapshot' ? (
                  <Select
                    value={activeSnapshotId}
                    disabled={!snapshots?.length}
                    onChange={(event) => setSnapshotId(event.target.value)}
                  >
                    {!snapshots?.length && <option value="">No ready snapshots</option>}
                    {snapshots?.map((item) => (
                      <option key={item.snapshot_id} value={item.snapshot_id}>
                        #{item.snapshot_id} · {item.name}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Select
                    value={activeEvaluationSetId}
                    disabled={!evaluationSets?.length}
                    onChange={(event) => setEvaluationSetId(event.target.value)}
                  >
                    {!evaluationSets?.length && <option value="">No evaluation sets</option>}
                    {evaluationSets?.map((item) => (
                      <option key={item.id} value={item.id}>
                        #{item.id} · {item.name} · {item.sample_count.toLocaleString()} rows
                      </option>
                    ))}
                  </Select>
                )}
              </VStack>

              <RadioGroup
                label="File format"
                value={format}
                onChange={(val) => setFormat(val as ExportFormat)}
              >
                {formatOptions.map((option) => (
                  <RadioItem
                    key={option.value}
                    label={option.label}
                    value={option.value}
                    description={option.detail}
                  />
                ))}
              </RadioGroup>

              <Checkbox
                label={source === 'snapshot' ? 'Include manifest.json' : 'Include evaluation_set.json'}
                checked={isHuggingFace || includeManifest}
                disabled={isHuggingFace}
                onChange={(checked) => setIncludeManifest(checked)}
              />
            </VStack>

            <VStack gap={4}>
              <VStack gap={2}>
                <HStack vAlign="center" hAlign="between">
                  <Text type="label" weight="medium">Include columns</Text>
                  <HStack gap={3}>
                    <Button variant="ghost" onClick={() => setSelectedColumns([...allColumns])}>
                      All
                    </Button>
                    <Button variant="ghost" onClick={() => setSelectedColumns([...translationColumns])}>
                      Translation only
                    </Button>
                  </HStack>
                </HStack>
                <Grid columns={2} gap={3}>
                  {groupedColumns.map(({ group, columns: groupColumns }) => (
                    <VStack key={group} gap={1.5}>
                      <Text type="supporting" weight="semibold">{group.toUpperCase()}</Text>
                      <VStack gap={1}>
                        {groupColumns.map((column) => (
                          <Checkbox
                            key={column.id}
                            label={column.label}
                            checked={selectedColumns.includes(column.id)}
                            onChange={() => toggle(column.id, selectedColumns, setSelectedColumns)}
                          />
                        ))}
                      </VStack>
                    </VStack>
                  ))}
                </Grid>
              </VStack>

              {source === 'snapshot' && (
                <VStack gap={2}>
                  <Text type="label" weight="medium">Splits</Text>
                  <HStack gap={4}>
                    {splitOptions.map((split) => (
                      <Checkbox
                        key={split}
                        label={split}
                        checked={effectiveSplits.includes(split)}
                        disabled={isHuggingFace}
                        onChange={() => toggle(split, selectedSplits, setSelectedSplits)}
                      />
                    ))}
                  </HStack>
                  {isHuggingFace && (
                    <Text type="supporting" color="secondary">
                      Hugging Face packages always include all three splits and a dataset card. After extracting, use <Text type="code">load_dataset('path/to/folder')</Text>.
                    </Text>
                  )}
                </VStack>
              )}

              <HStack gap={2}>
                {customUrl ? (
                  <a href={customUrl} download>
                    <Button variant="primary">Download custom package (.zip)</Button>
                  </a>
                ) : (
                  <Button variant="primary" disabled>Download custom package (.zip)</Button>
                )}
              </HStack>
              {!selectedAvailableColumns.length && <Text type="supporting" color="secondary">Choose at least one column to continue.</Text>}
              {source === 'snapshot' && !effectiveSplits.length && <Text type="supporting" color="secondary">Choose at least one split to continue.</Text>}
            </VStack>
          </Grid>
        </VStack>
      </Card>

      <VStack gap={2}>
        <Heading level={4}>Original snapshot files</Heading>
        <DataTable
          loading={snapshotsLoading}
          rows={snapshots as unknown as Row[]}
          empty="No ready snapshots yet."
          columns={[
            { key: 'snapshot_id', header: 'Snapshot', width: '90px' },
            { key: 'name', header: 'Name' },
            {
              key: 'counts',
              header: 'train / validation / test',
              render: (row) => {
                const counts = row.counts as Record<string, number>
                return `${counts?.train ?? 0} / ${counts?.validation ?? 0} / ${counts?.test ?? 0}`
              },
            },
            { key: 'prefix_uri', header: 'Location' },
            {
              key: 'download',
              header: 'Download as-is',
              render: (row) => ['train', 'validation', 'test', 'manifest'].map((file) => directLink(row.snapshot_id as number, file)),
            },
          ]}
        />
      </VStack>

      <VStack gap={2}>
        <Heading level={4}>Original evaluation-set files</Heading>
        <DataTable
          loading={evaluationSetsLoading}
          rows={evaluationSets as unknown as Row[]}
          empty="No evaluation sets yet."
          columns={[
            { key: 'id', header: 'Evaluation set', width: '120px' },
            { key: 'name', header: 'Name' },
            { key: 'kind', header: 'Kind' },
            { key: 'sample_count', header: 'Rows' },
            { key: 'parquet_uri', header: 'Location' },
            {
              key: 'download',
              header: 'Download as-is',
              render: (row) => (
                <a
                  className="text-sm text-slate-700 underline decoration-slate-300 underline-offset-2 hover:text-slate-950"
                  href={api.downloadUrl(`/exports/evaluation-sets/${row.id}/data`)}
                >
                  data
                </a>
              ),
            },
          ]}
        />
      </VStack>
    </VStack>
  )
}
