import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'
import { CodeBlock } from '@astryxdesign/core/CodeBlock'

import { DataTable } from '@/components/DataTable'
import { EditEntity } from '@/components/EditEntity'
import { Badge, Button, Card, Checkbox, ErrorBox, Field, Input, PageHeader, Select } from '@/components/ui'
import { useCreate, useList, usePatch, useRemove } from '@/hooks/useResource'
import { api } from '@/lib/api'
import type { Batch, BatchRule, Dataset, DatasetReservation, Filters, Row } from '@/types'

const parseIds = (value: string): number[] =>
  value
    .split(',')
    .map((item) => Number(item.trim()))
    .filter((item) => Number.isFinite(item) && item > 0)

const parseList = (value: string): string[] =>
  value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)

type EditableRule = { batch_id: string; mode: BatchRule['mode']; value: string }

export default function Datasets() {
  const queryClient = useQueryClient()
  const datasets = useList<Dataset>('datasets')
  const batches = useList<Batch>('batches', { limit: 200 })
  const create = useCreate<Dataset>('datasets')
  const remove = useRemove('datasets')
  const patch = usePatch<Dataset>('datasets')
  
  const [editing, setEditing] = useState<Dataset | null>(null)
  const [deletingDataset, setDeletingDataset] = useState<Dataset | null>(null)
  const [form, setForm] = useState({
    name: '',
    description: '',
    mode: 'whole' as 'whole' | 'custom',
    batch_ids: '',
    composition_seed: '42',
    src_langs: '',
    tgt_langs: '',
    domains: '',
    min_quality: '',
  })
  const [rules, setRules] = useState<EditableRule[]>([
    { batch_id: '', mode: 'all', value: '' },
  ])
  const [inspect, setInspect] = useState<number | null>(null)
  const [inspectTab, setInspectTab] = useState<'allocations' | 'reservation' | 'stats' | 'preview'>('allocations')
  
  const [reservation, setReservation] = useState({
    selector: 'contamination_safe',
    percent: '1',
    max_samples: '10000',
    target_count: '',
    seed: '42',
    contamination_scope: 'registry',
    confirmed: false,
  })

  const selectedInspectDataset = datasets.data?.find((d) => d.id === inspect)

  const stats = useQuery({
    queryKey: ['dataset-stats', inspect],
    queryFn: () => api.get<Record<string, unknown>>(`/datasets/${inspect}/statistics`),
    enabled: inspect !== null && inspectTab === 'stats',
  })
  const preview = useQuery({
    queryKey: ['dataset-preview', inspect],
    queryFn: () => api.get<Row[]>(`/datasets/${inspect}/preview`, { limit: 20 }),
    enabled: inspect !== null && inspectTab === 'preview',
  })
  const allocations = useQuery({
    queryKey: ['dataset-allocations', inspect],
    queryFn: () => api.get<Record<string, number>>(`/datasets/${inspect}/allocation-summary`),
    enabled: inspect !== null,
  })
  const reservations = useQuery({
    queryKey: ['dataset-reservations', inspect],
    queryFn: () => api.get<DatasetReservation[]>(`/datasets/${inspect}/reservations`),
    enabled: inspect !== null && inspectTab === 'reservation',
    refetchInterval: 3000,
  })
  const reserve = useMutation({
    mutationFn: (body: Record<string, unknown>) =>
      api.post<DatasetReservation>(`/datasets/${inspect}/reservations`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['dataset-reservations', inspect] })
      queryClient.invalidateQueries({ queryKey: ['dataset-allocations', inspect] })
      setReservation((current) => ({ ...current, confirmed: false }))
    },
  })

  const setRule = (index: number, patch: Partial<EditableRule>) =>
    setRules((current) =>
      current.map((rule, ruleIndex) => (ruleIndex === index ? { ...rule, ...patch } : rule)),
    )

  const selectedBatchIdArray = parseIds(form.batch_ids)

  const toggleBatchId = (id: number) => {
    let next: number[]
    if (selectedBatchIdArray.includes(id)) {
      next = selectedBatchIdArray.filter((item) => item !== id)
    } else {
      next = [...selectedBatchIdArray, id]
    }
    setForm({ ...form, batch_ids: next.join(', ') })
  }

  const selectAllBatches = () => {
    if (!batches.data) return
    setForm({ ...form, batch_ids: batches.data.map((b) => b.id).join(', ') })
  }

  const clearBatchSelection = () => {
    setForm({ ...form, batch_ids: '' })
  }

  const renderFilterBadges = (filters?: Filters) => {
    if (!filters) return <Text type="supporting" color="secondary">—</Text>

    const badges: React.ReactNode[] = []

    if (filters.src_langs?.length || filters.tgt_langs?.length) {
      const src = filters.src_langs?.length ? filters.src_langs.join(', ') : '*'
      const tgt = filters.tgt_langs?.length ? filters.tgt_langs.join(', ') : '*'
      badges.push(
        <Badge key="langs" variant="info">
          {`${src} → ${tgt}`}
        </Badge>
      )
    }

    if (filters.domains?.length) {
      badges.push(
        <Badge key="domains" variant="neutral">
          {`Domain: ${filters.domains.join(', ')}`}
        </Badge>
      )
    }

    if (filters.min_quality !== null && filters.min_quality !== undefined) {
      badges.push(
        <Badge key="min_q" variant="neutral">
          {`Min Quality: ${filters.min_quality}`}
        </Badge>
      )
    }

    if (badges.length === 0) {
      return <Text type="supporting" color="secondary">All sample data</Text>
    }

    return <HStack gap={1} style={{ flexWrap: 'wrap' }}>{badges}</HStack>
  }

  return (
    <VStack gap={4}>
      <PageHeader
        title="Datasets"
        subtitle="Logical, reproducible combinations of immutable batches — no data copied"
      />
      <ErrorBox error={create.error} />
      <ErrorBox error={remove.error} />

      {/* Delete Confirmation Modal / Card */}
      {deletingDataset && (
        <Card style={{ borderColor: 'rgba(239, 68, 68, 0.4)', backgroundColor: 'rgba(239, 68, 68, 0.05)' }}>
          <VStack gap={3}>
            <HStack vAlign="center" hAlign="between">
              <Heading level={4} style={{ color: '#ef4444' }}>
                Confirm Deletion of Dataset #{deletingDataset.id}
              </Heading>
              <Button variant="ghost" onClick={() => setDeletingDataset(null)}>
                Cancel
              </Button>
            </HStack>
            <Text type="body">
              Are you sure you want to delete dataset <strong>"{deletingDataset.name}"</strong> (ID: {deletingDataset.id})? This logical definition will be permanently removed. (No underlying raw batch data or parquet files will be deleted).
            </Text>
            <HStack gap={2} vAlign="center">
              <Button
                variant="danger"
                disabled={remove.isPending}
                onClick={() => {
                  remove.mutate(deletingDataset.id, {
                    onSuccess: () => setDeletingDataset(null),
                  })
                }}
              >
                {remove.isPending ? 'Deleting…' : 'Confirm Delete'}
              </Button>
              <Button variant="ghost" onClick={() => setDeletingDataset(null)}>
                Keep Dataset
              </Button>
            </HStack>
          </VStack>
        </Card>
      )}

      {/* Create Dataset Card */}
      <Card>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            const batchRules = rules
              .filter((rule) => Number(rule.batch_id) > 0)
              .map((rule) => ({
                batch_id: Number(rule.batch_id),
                mode: rule.mode,
                value: rule.mode === 'all' ? null : Number(rule.value),
              }))
            create.mutate(
              {
                name: form.name,
                description: form.description || null,
                batch_ids: form.mode === 'whole' ? selectedBatchIdArray : [],
                batch_rules: form.mode === 'custom' ? batchRules : [],
                composition_seed: Number(form.composition_seed),
                filters: {
                  src_langs: parseList(form.src_langs),
                  tgt_langs: parseList(form.tgt_langs),
                  domains: parseList(form.domains),
                  min_quality: form.min_quality ? Number(form.min_quality) : null,
                },
              },
              {
                onSuccess: () => {
                  setForm({
                    name: '',
                    description: '',
                    mode: 'whole',
                    batch_ids: '',
                    composition_seed: '42',
                    src_langs: '',
                    tgt_langs: '',
                    domains: '',
                    min_quality: '',
                  })
                },
              }
            )
          }}
        >
          <VStack gap={4}>
            <Heading level={4}>Create Logical Dataset</Heading>
            <Grid columns={4} gap={3}>
              <Field label="Dataset Name">
                <Input
                  required
                  placeholder="e.g. WMT23 En-Fa Medical Train"
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>
              <Field label="Composition Mode">
                <Select
                  value={form.mode}
                  onChange={(event) =>
                    setForm({ ...form, mode: event.target.value as 'whole' | 'custom' })
                  }
                >
                  <option value="whole">Whole batches</option>
                  <option value="custom">Custom amount per batch</option>
                </Select>
              </Field>
              <Field label="Composition Seed">
                <Input
                  type="number"
                  value={form.composition_seed}
                  onChange={(event) => setForm({ ...form, composition_seed: event.target.value })}
                />
              </Field>
              <Field label="Description">
                <Input
                  placeholder="Optional dataset notes"
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
              </Field>
            </Grid>

            {form.mode === 'whole' ? (
              <VStack gap={2}>
                <HStack vAlign="center" hAlign="between">
                  <Text type="supporting" weight="medium">
                    Batch Selection {selectedBatchIdArray.length > 0 ? `(${selectedBatchIdArray.length} selected)` : '(All ready batches included by default)'}
                  </Text>
                  <HStack gap={2}>
                    <Button type="button" variant="ghost" onClick={selectAllBatches}>
                      Select All
                    </Button>
                    <Button type="button" variant="ghost" onClick={clearBatchSelection}>
                      Clear Selection
                    </Button>
                  </HStack>
                </HStack>

                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
                    gap: '0.5rem',
                    maxHeight: '180px',
                    overflowY: 'auto',
                    padding: '0.75rem',
                    border: '1px solid rgba(255, 255, 255, 0.1)',
                    borderRadius: '8px',
                    backgroundColor: 'rgba(0, 0, 0, 0.2)',
                  }}
                >
                  {batches.data?.map((batch) => {
                    const isSelected = selectedBatchIdArray.includes(batch.id)
                    return (
                      <div
                        key={batch.id}
                        onClick={() => toggleBatchId(batch.id)}
                        style={{
                          cursor: 'pointer',
                          padding: '0.5rem 0.75rem',
                          borderRadius: '6px',
                          border: isSelected
                            ? '1px solid rgba(59, 130, 246, 0.6)'
                            : '1px solid rgba(255, 255, 255, 0.08)',
                          backgroundColor: isSelected
                            ? 'rgba(59, 130, 246, 0.15)'
                            : 'rgba(255, 255, 255, 0.03)',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          userSelect: 'none',
                          transition: 'all 0.15s ease',
                        }}
                      >
                        <VStack gap={0}>
                          <Text type="body" weight="medium" style={{ fontSize: '0.85rem' }}>
                            {batch.name}
                          </Text>
                          <Text type="supporting" color="secondary" style={{ fontSize: '0.75rem' }}>
                            ID: {batch.id} · {batch.sample_count.toLocaleString()} rows
                          </Text>
                        </VStack>
                        <span style={{ fontSize: '0.85rem', fontWeight: 'bold', color: isSelected ? '#60a5fa' : '#6b7280' }}>
                          {isSelected ? '✓' : '+'}
                        </span>
                      </div>
                    )
                  })}
                </div>
              </VStack>
            ) : (
              <Card>
                <VStack gap={3}>
                  <HStack vAlign="center" hAlign="between">
                    <VStack gap={1}>
                      <Heading level={5}>Batch Contributions</Heading>
                      <Text type="supporting" color="secondary">
                        Percent and row count rules are exact and reproducible for the given composition seed.
                      </Text>
                    </VStack>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setRules([...rules, { batch_id: '', mode: 'all', value: '' }])
                      }
                    >
                      Add Batch Rule
                    </Button>
                  </HStack>
                  <VStack gap={2}>
                    {rules.map((rule, index) => (
                      <Grid key={index} columns={4} gap={2} align="end">
                        <Field label="Batch">
                          <Select
                            required
                            value={rule.batch_id}
                            onChange={(event) => setRule(index, { batch_id: event.target.value })}
                          >
                            <option value="">Select batch…</option>
                            {batches.data?.map((batch) => (
                              <option key={batch.id} value={batch.id}>
                                {batch.name} (ID: {batch.id}) · {batch.sample_count.toLocaleString()} rows
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <Field label="Contribution Mode">
                          <Select
                            value={rule.mode}
                            onChange={(event) =>
                              setRule(index, {
                                mode: event.target.value as BatchRule['mode'],
                                value: event.target.value === 'all' ? '' : rule.value,
                              })
                            }
                          >
                            <option value="all">All Rows</option>
                            <option value="percent">Percent (%)</option>
                            <option value="count">Row Count</option>
                          </Select>
                        </Field>
                        <Field label={rule.mode === 'percent' ? 'Percent' : 'Rows'}>
                          <Input
                            disabled={rule.mode === 'all'}
                            required={rule.mode !== 'all'}
                            type="number"
                            min={rule.mode === 'percent' ? 0.000001 : 1}
                            max={rule.mode === 'percent' ? 100 : undefined}
                            step={rule.mode === 'percent' ? 'any' : 1}
                            placeholder={rule.mode === 'percent' ? '50' : '500000'}
                            value={rule.value}
                            onChange={(event) => setRule(index, { value: event.target.value })}
                          />
                        </Field>
                        <HStack vAlign="end">
                          <Button
                            type="button"
                            variant="ghost"
                            disabled={rules.length === 1}
                            onClick={() => setRules(rules.filter((_, ruleIndex) => ruleIndex !== index))}
                          >
                            Remove Rule
                          </Button>
                        </HStack>
                      </Grid>
                    ))}
                  </VStack>
                </VStack>
              </Card>
            )}

            <Grid columns={4} gap={3}>
              <Field label="Source Languages">
                <Input
                  placeholder="e.g. en"
                  value={form.src_langs}
                  onChange={(event) => setForm({ ...form, src_langs: event.target.value })}
                />
              </Field>
              <Field label="Target Languages">
                <Input
                  placeholder="e.g. fa, de"
                  value={form.tgt_langs}
                  onChange={(event) => setForm({ ...form, tgt_langs: event.target.value })}
                />
              </Field>
              <Field label="Domains">
                <Input
                  placeholder="e.g. medical, legal"
                  value={form.domains}
                  onChange={(event) => setForm({ ...form, domains: event.target.value })}
                />
              </Field>
              <Field label="Min Quality Score">
                <Input
                  type="number"
                  step="0.01"
                  placeholder="e.g. 0.8"
                  value={form.min_quality}
                  onChange={(event) => setForm({ ...form, min_quality: event.target.value })}
                />
              </Field>
            </Grid>
            <HStack>
              <Button disabled={create.isPending}>
                {create.isPending ? 'Creating…' : 'Create Dataset'}
              </Button>
            </HStack>
          </VStack>
        </form>
      </Card>

      {/* Edit Entity Overlay */}
      {editing && (
        <EditEntity
          title={`Edit Dataset #${editing.id}: ${editing.name}`}
          fields={[
            { key: 'name', label: 'Dataset Name', required: true },
            { key: 'composition_seed', label: 'Composition Seed', type: 'text', required: true },
            { key: 'src_langs', label: 'Source Languages (comma list)', type: 'text' },
            { key: 'tgt_langs', label: 'Target Languages (comma list)', type: 'text' },
            { key: 'domains', label: 'Domains (comma list)', type: 'text' },
            { key: 'min_quality', label: 'Min Quality Score', type: 'text' },
            { key: 'description', label: 'Description', type: 'textarea' },
          ]}
          initial={{
            name: editing.name,
            composition_seed: String(editing.composition_seed ?? 42),
            src_langs: editing.filters?.src_langs?.join(', ') ?? '',
            tgt_langs: editing.filters?.tgt_langs?.join(', ') ?? '',
            domains: editing.filters?.domains?.join(', ') ?? '',
            min_quality: editing.filters?.min_quality !== null ? String(editing.filters?.min_quality ?? '') : '',
            description: editing.description ?? '',
          }}
          error={patch.error}
          isSaving={patch.isPending}
          onClose={() => setEditing(null)}
          onSave={(values) =>
            patch.mutate(
              {
                id: editing.id,
                body: {
                  name: values.name,
                  description: values.description || null,
                  batch_ids: editing.batch_ids,
                  batch_rules: editing.batch_rules ?? [],
                  composition_seed: Number(values.composition_seed || editing.composition_seed),
                  filters: {
                    src_langs: parseList(values.src_langs),
                    tgt_langs: parseList(values.tgt_langs),
                    domains: parseList(values.domains),
                    min_quality: values.min_quality ? Number(values.min_quality) : null,
                  },
                },
              },
              { onSuccess: () => setEditing(null) }
            )
          }
        />
      )}

      {/* Main Datasets Table */}
      <DataTable
        loading={datasets.isLoading}
        rows={datasets.data as unknown as Row[]}
        columns={[
          { key: 'id', header: 'ID', width: '60px' },
          { key: 'name', header: 'Name' },
          {
            key: 'batch_rules',
            header: 'Composition',
            render: (row) => {
              const batchRules = row.batch_rules as BatchRule[]
              if (!batchRules?.length) {
                const ids = row.batch_ids as number[]
                return ids?.length ? `Batches [${ids.join(', ')}]` : 'All ready batches'
              }
              return batchRules
                .map((rule) =>
                  rule.mode === 'all'
                    ? `Batch #${rule.batch_id}: All`
                    : `Batch #${rule.batch_id}: ${rule.value}${rule.mode === 'percent' ? '%' : ' rows'}`,
                )
                .join(' · ')
            },
          },
          { key: 'composition_seed', header: 'Seed', width: '80px' },
          {
            key: 'filters',
            header: 'Filters',
            render: (row) => renderFilterBadges(row.filters as Filters),
          },
          {
            key: 'actions',
            header: 'Actions',
            render: (row) => {
              const isInspected = inspect === (row.id as number)
              return (
                <HStack gap={2}>
                  <Button variant="ghost" onClick={() => setEditing(row as unknown as Dataset)}>
                    Edit
                  </Button>
                  <Button
                    variant={isInspected ? 'primary' : 'ghost'}
                    onClick={() => {
                      if (isInspected) {
                        setInspect(null)
                      } else {
                        setInspect(row.id as number)
                        setInspectTab('allocations')
                      }
                    }}
                  >
                    {isInspected ? 'Close' : 'Inspect'}
                  </Button>
                  <Button
                    variant="danger"
                    onClick={() => setDeletingDataset(row as unknown as Dataset)}
                  >
                    Delete
                  </Button>
                </HStack>
              )
            },
          },
        ]}
      />

      {/* Progressive Disclosure Inspect Panel */}
      {inspect !== null && selectedInspectDataset && (
        <Card style={{ borderColor: 'rgba(59, 130, 246, 0.4)', backgroundColor: 'rgba(15, 23, 42, 0.8)' }}>
          <VStack gap={4}>
            {/* Header & Tab Selector Bar */}
            <HStack vAlign="center" hAlign="between" style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.1)', paddingBottom: '0.75rem' }}>
              <VStack gap={1}>
                <HStack gap={2} vAlign="center">
                  <Heading level={3}>Dataset #{selectedInspectDataset.id}: {selectedInspectDataset.name}</Heading>
                  <Badge variant="info">Inspect Mode</Badge>
                </HStack>
                <Text type="supporting" color="secondary">
                  Composition Seed: {selectedInspectDataset.composition_seed} · Logical definition only (no data copied)
                </Text>
              </VStack>
              <Button variant="ghost" onClick={() => setInspect(null)}>
                Close Inspect
              </Button>
            </HStack>

            {/* Navigation Tabs */}
            <HStack gap={2} style={{ borderBottom: '1px solid rgba(255, 255, 255, 0.08)', paddingBottom: '0.5rem' }}>
              {[
                { id: 'allocations', label: 'Overview & Allocations' },
                { id: 'reservation', label: 'Reserve Evaluation' },
                { id: 'stats', label: 'Trainable Statistics' },
                { id: 'preview', label: 'Sample Preview' },
              ].map((tab) => {
                const isActive = inspectTab === tab.id
                return (
                  <Button
                    key={tab.id}
                    type="button"
                    variant={isActive ? 'primary' : 'ghost'}
                    onClick={() => setInspectTab(tab.id as typeof inspectTab)}
                  >
                    {tab.label}
                  </Button>
                )
              })}
            </HStack>

            {/* TAB 1: OVERVIEW & ALLOCATIONS */}
            {inspectTab === 'allocations' && (
              <VStack gap={4}>
                <VStack gap={2}>
                  <Heading level={4}>Allocation Summary</Heading>
                  <Text type="supporting" color="secondary">
                    Snapshot exports use only rows marked TRAINABLE. RESERVED_EVALUATION samples are excluded automatically.
                  </Text>
                </VStack>
                <Grid columns={5} gap={2}>
                  {['composed', 'trainable', 'reserved_evaluation', 'quarantined', 'ignored'].map(
                    (key) => {
                      const count = allocations.data?.[key]
                      const label = key.replace('_', ' ').toUpperCase()
                      return (
                        <Card key={key}>
                          <VStack gap={1}>
                            <Text type="supporting" color="secondary" weight="medium" style={{ fontSize: '0.75rem' }}>
                              {label}
                            </Text>
                            <Heading level={4}>
                              {allocations.isLoading ? '…' : count !== undefined ? count.toLocaleString() : '—'}
                            </Heading>
                          </VStack>
                        </Card>
                      )
                    }
                  )}
                </Grid>
                <Card>
                  <VStack gap={2}>
                    <Heading level={5}>Active Dataset Filters</Heading>
                    {renderFilterBadges(selectedInspectDataset.filters)}
                  </VStack>
                </Card>
              </VStack>
            )}

            {/* TAB 2: RESERVE EVALUATION DATA */}
            {inspectTab === 'reservation' && (
              <VStack gap={4}>
                <Card>
                  <VStack gap={3}>
                    <Heading level={4}>Reserve Evaluation Data</Heading>
                    <Text type="supporting" color="secondary">
                      Reserves a portion of this dataset for reusable benchmarks. Reservation is an irreversible allocation state to prevent training contamination.
                    </Text>
                    <ErrorBox error={reserve.error} />
                    <form
                      onSubmit={(event) => {
                        event.preventDefault()
                        reserve.mutate({
                          selector: reservation.selector,
                          percent: Number(reservation.percent),
                          max_samples: Number(reservation.max_samples),
                          target_count: reservation.target_count
                            ? Number(reservation.target_count)
                            : null,
                          seed: Number(reservation.seed),
                          contamination_scope: reservation.contamination_scope,
                          confirm_irreversible: true,
                        })
                      }}
                    >
                      <Grid columns={4} gap={3} align="end">
                        <Field label="Reservation Selector">
                          <Select
                            value={reservation.selector}
                            onChange={(event) =>
                              setReservation({ ...reservation, selector: event.target.value })
                            }
                          >
                            <option value="contamination_safe">contamination_safe (Recommended)</option>
                            <option value="heuristic">heuristic</option>
                            <option value="random">random</option>
                          </Select>
                        </Field>
                        <Field label="Evaluation Percent (%)">
                          <Input
                            type="number"
                            min="0"
                            max="100"
                            step="any"
                            value={reservation.percent}
                            onChange={(event) =>
                              setReservation({ ...reservation, percent: event.target.value })
                            }
                          />
                        </Field>
                        <Field label="Maximum Samples Cap">
                          <Input
                            type="number"
                            min="0"
                            value={reservation.max_samples}
                            onChange={(event) =>
                              setReservation({ ...reservation, max_samples: event.target.value })
                            }
                          />
                        </Field>
                        <Field label="Exact Target (Optional)">
                          <Input
                            type="number"
                            min="0"
                            placeholder="e.g. 20000"
                            value={reservation.target_count}
                            onChange={(event) =>
                              setReservation({ ...reservation, target_count: event.target.value })
                            }
                          />
                        </Field>
                        <Field label="Reservation Seed">
                          <Input
                            type="number"
                            value={reservation.seed}
                            onChange={(event) =>
                              setReservation({ ...reservation, seed: event.target.value })
                            }
                          />
                        </Field>
                        <Field label="Contamination Scan Scope">
                          <Select
                            value={reservation.contamination_scope}
                            onChange={(event) =>
                              setReservation({ ...reservation, contamination_scope: event.target.value })
                            }
                          >
                            <option value="registry">All ready batches in registry</option>
                            <option value="composition">This dataset composition only</option>
                          </Select>
                        </Field>
                        <Checkbox
                          label="I understand this reservation is irreversible"
                          checked={reservation.confirmed}
                          onChange={(confirmed) =>
                            setReservation({ ...reservation, confirmed })
                          }
                        />
                        <HStack vAlign="end">
                          <Button disabled={reserve.isPending || !reservation.confirmed}>
                            {reserve.isPending ? 'Reserving…' : 'Start Reservation'}
                          </Button>
                        </HStack>
                      </Grid>
                    </form>
                  </VStack>
                </Card>

                {/* Reservation History */}
                <Card>
                  <VStack gap={3}>
                    <Heading level={4}>Reservation History</Heading>
                    <VStack gap={2}>
                      {reservations.data?.length ? (
                        reservations.data.map((item) => (
                          <Card key={item.id}>
                            <VStack gap={2}>
                              <HStack vAlign="center" hAlign="between">
                                <HStack gap={2} vAlign="center">
                                  <Text type="body" weight="medium">Reservation #{item.id}</Text>
                                  <Badge>{item.selector}</Badge>
                                </HStack>
                                <Badge>{item.status}</Badge>
                              </HStack>
                              {item.error && (
                                <Text type="body" style={{ color: '#ef4444' }}>
                                  {item.error}
                                </Text>
                              )}
                              {item.report && (
                                <Grid columns={4} gap={2}>
                                  {Object.entries(item.report).map(([key, val]) => (
                                    <VStack key={key} gap={0}>
                                      <Text type="supporting" color="secondary" style={{ fontSize: '0.75rem' }}>
                                        {key}
                                      </Text>
                                      <Text type="body" weight="medium">
                                        {typeof val === 'object' ? JSON.stringify(val) : String(val)}
                                      </Text>
                                    </VStack>
                                  ))}
                                </Grid>
                              )}
                            </VStack>
                          </Card>
                        ))
                      ) : (
                        <Text type="supporting" color="secondary">No dataset-level reservations yet.</Text>
                      )}
                    </VStack>
                  </VStack>
                </Card>
              </VStack>
            )}

            {/* TAB 3: TRAINABLE STATISTICS */}
            {inspectTab === 'stats' && (
              <Card>
                <VStack gap={3}>
                  <Heading level={4}>Trainable Dataset Statistics</Heading>
                  {stats.isLoading ? (
                    <Text type="body">Loading statistics…</Text>
                  ) : stats.data ? (
                    <VStack gap={3}>
                      <Grid columns={3} gap={3}>
                        {Object.entries(stats.data)
                          .filter(([_, val]) => typeof val === 'number' || typeof val === 'string')
                          .map(([key, val]) => (
                            <Card key={key}>
                              <VStack gap={1}>
                                <Text type="supporting" color="secondary" weight="medium" style={{ fontSize: '0.75rem' }}>
                                  {key.replace('_', ' ').toUpperCase()}
                                </Text>
                                <Heading level={4}>
                                  {typeof val === 'number' ? val.toLocaleString() : String(val)}
                                </Heading>
                              </VStack>
                            </Card>
                          ))}
                      </Grid>
                      <CodeBlock
                        language="json"
                        code={JSON.stringify(stats.data, null, 2)}
                      />
                    </VStack>
                  ) : (
                    <Text type="supporting" color="secondary">No statistics available.</Text>
                  )}
                </VStack>
              </Card>
            )}

            {/* TAB 4: SAMPLE PREVIEW */}
            {inspectTab === 'preview' && (
              <Card>
                <VStack gap={3}>
                  <Heading level={4}>Trainable Sample Preview (First 20 rows)</Heading>
                  <DataTable
                    loading={preview.isLoading}
                    rows={preview.data}
                    columns={[
                      { key: 'sample_id', header: 'Sample ID', width: '90px' },
                      { key: 'source_text', header: 'Source Sentence' },
                      { key: 'target_text', header: 'Target Translation' },
                    ]}
                  />
                </VStack>
              </Card>
            )}
          </VStack>
        </Card>
      )}
    </VStack>
  )
}
