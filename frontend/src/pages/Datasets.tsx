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
import type { Batch, BatchRule, Dataset, DatasetReservation, Row } from '@/types'

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
  const [reservation, setReservation] = useState({
    selector: 'contamination_safe',
    percent: '1',
    max_samples: '10000',
    target_count: '',
    seed: '42',
    contamination_scope: 'registry',
    confirmed: false,
  })

  const stats = useQuery({
    queryKey: ['dataset-stats', inspect],
    queryFn: () => api.get<Record<string, unknown>>(`/datasets/${inspect}/statistics`),
    enabled: inspect !== null,
  })
  const preview = useQuery({
    queryKey: ['dataset-preview', inspect],
    queryFn: () => api.get<Row[]>(`/datasets/${inspect}/preview`, { limit: 20 }),
    enabled: inspect !== null,
  })
  const allocations = useQuery({
    queryKey: ['dataset-allocations', inspect],
    queryFn: () => api.get<Record<string, number>>(`/datasets/${inspect}/allocation-summary`),
    enabled: inspect !== null,
  })
  const reservations = useQuery({
    queryKey: ['dataset-reservations', inspect],
    queryFn: () => api.get<DatasetReservation[]>(`/datasets/${inspect}/reservations`),
    enabled: inspect !== null,
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

  return (
    <VStack gap={4}>
      <PageHeader
        title="Datasets"
        subtitle="Logical, reproducible combinations of immutable batches — no data copied"
      />
      <ErrorBox error={create.error} />

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
            create.mutate({
              name: form.name,
              description: form.description || null,
              batch_ids: form.mode === 'whole' ? parseIds(form.batch_ids) : [],
              batch_rules: form.mode === 'custom' ? batchRules : [],
              composition_seed: Number(form.composition_seed),
              filters: {
                src_langs: parseList(form.src_langs),
                tgt_langs: parseList(form.tgt_langs),
                domains: parseList(form.domains),
                min_quality: form.min_quality ? Number(form.min_quality) : null,
              },
            })
          }}
        >
          <VStack gap={4}>
            <Grid columns={4} gap={3}>
              <Field label="Name">
                <Input
                  required
                  value={form.name}
                  onChange={(event) => setForm({ ...form, name: event.target.value })}
                />
              </Field>
              <Field label="Composition">
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
              <Field label="Composition seed">
                <Input
                  type="number"
                  value={form.composition_seed}
                  onChange={(event) => setForm({ ...form, composition_seed: event.target.value })}
                />
              </Field>
              <Field label="Description">
                <Input
                  value={form.description}
                  onChange={(event) => setForm({ ...form, description: event.target.value })}
                />
              </Field>
            </Grid>

            {form.mode === 'whole' ? (
              <Field
                label={`Batch ids (available: ${batches.data?.map((batch) => batch.id).join(', ') ?? '—'})`}
              >
                <Input
                  placeholder="1,2  (empty keeps the existing all-ready-batches behavior)"
                  value={form.batch_ids}
                  onChange={(event) => setForm({ ...form, batch_ids: event.target.value })}
                />
              </Field>
            ) : (
              <Card>
                <VStack gap={3}>
                  <HStack vAlign="center" hAlign="between">
                    <VStack gap={1}>
                      <Heading level={5}>Batch contributions</Heading>
                      <Text type="supporting" color="secondary">
                        Percent and count selections are exact and reproducible for the composition seed.
                      </Text>
                    </VStack>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() =>
                        setRules([...rules, { batch_id: '', mode: 'all', value: '' }])
                      }
                    >
                      Add batch
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
                            <option value="">select…</option>
                            {batches.data?.map((batch) => (
                              <option key={batch.id} value={batch.id}>
                                {batch.name} · {batch.sample_count.toLocaleString()} rows
                              </option>
                            ))}
                          </Select>
                        </Field>
                        <Field label="Amount">
                          <Select
                            value={rule.mode}
                            onChange={(event) =>
                              setRule(index, {
                                mode: event.target.value as BatchRule['mode'],
                                value: event.target.value === 'all' ? '' : rule.value,
                              })
                            }
                          >
                            <option value="all">All</option>
                            <option value="percent">Percent</option>
                            <option value="count">Row count</option>
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
                            Remove
                          </Button>
                        </HStack>
                      </Grid>
                    ))}
                  </VStack>
                </VStack>
              </Card>
            )}

            <Grid columns={4} gap={3}>
              <Field label="Source langs">
                <Input
                  placeholder="en"
                  value={form.src_langs}
                  onChange={(event) => setForm({ ...form, src_langs: event.target.value })}
                />
              </Field>
              <Field label="Target langs">
                <Input
                  placeholder="fa"
                  value={form.tgt_langs}
                  onChange={(event) => setForm({ ...form, tgt_langs: event.target.value })}
                />
              </Field>
              <Field label="Domains">
                <Input
                  placeholder="medical,legal"
                  value={form.domains}
                  onChange={(event) => setForm({ ...form, domains: event.target.value })}
                />
              </Field>
              <Field label="Min quality">
                <Input
                  type="number"
                  step="0.01"
                  value={form.min_quality}
                  onChange={(event) => setForm({ ...form, min_quality: event.target.value })}
                />
              </Field>
            </Grid>
            <HStack>
              <Button disabled={create.isPending}>Create dataset</Button>
            </HStack>
          </VStack>
        </form>
      </Card>

      {editing && (
        <EditEntity
          title={`Edit dataset ${editing.name}`}
          fields={[
            { key: 'name', label: 'Name', required: true },
            { key: 'description', label: 'Description', type: 'textarea' },
          ]}
          initial={{ name: editing.name, description: editing.description ?? '' }}
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
                  composition_seed: editing.composition_seed,
                  filters: editing.filters,
                },
              },
              { onSuccess: () => setEditing(null) },
            )
          }
        />
      )}

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
              if (!batchRules?.length) return ((row.batch_ids as number[]).join(', ') || 'all')
              return batchRules
                .map((rule) =>
                  rule.mode === 'all'
                    ? `${rule.batch_id}: all`
                    : `${rule.batch_id}: ${rule.value}${rule.mode === 'percent' ? '%' : ''}`,
                )
                .join(' · ')
            },
          },
          { key: 'composition_seed', header: 'Seed' },
          {
            key: 'filters',
            header: 'Filters',
            render: (row) => (
              <Text type="code">{JSON.stringify(row.filters).slice(0, 90)}</Text>
            ),
          },
          {
            key: 'actions',
            header: '',
            render: (row) => (
              <HStack gap={2}>
                <Button variant="ghost" onClick={() => setEditing(row as unknown as Dataset)}>
                  Edit
                </Button>
                <Button variant="ghost" onClick={() => setInspect(row.id as number)}>
                  Inspect
                </Button>
                <Button variant="danger" onClick={() => remove.mutate(row.id as number)}>
                  Delete
                </Button>
              </HStack>
            ),
          },
        ]}
      />

      {inspect !== null && (
        <VStack gap={4}>
          <Card>
            <VStack gap={3}>
              <HStack vAlign="center" hAlign="between">
                <VStack gap={1}>
                  <Heading level={4}>Composition and current allocations</Heading>
                  <Text type="supporting" color="secondary">
                    Snapshot splits use only the trainable rows shown here.
                  </Text>
                </VStack>
                <Button variant="ghost" onClick={() => setInspect(null)}>
                  Close
                </Button>
              </HStack>
              <Grid columns={5} gap={2}>
                {['composed', 'trainable', 'reserved_evaluation', 'quarantined', 'ignored'].map(
                  (key) => (
                    <Card key={key}>
                      <VStack gap={1}>
                        <Text type="supporting" color="secondary" weight="medium">
                          {key.replace('_', ' ').toUpperCase()}
                        </Text>
                        <Heading level={4}>
                          {allocations.data?.[key]?.toLocaleString() ?? '—'}
                        </Heading>
                      </VStack>
                    </Card>
                  ),
                )}
              </Grid>
            </VStack>
          </Card>

          <Card>
            <VStack gap={3}>
              <Heading level={4}>Reserve evaluation data</Heading>
              <Text type="supporting" color="secondary">
                The exact target overrides percent and cap. Registry scope is safest for reusable
                benchmarks. Reservation and quarantine are permanent global allocations.
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
                  <Field label="Selector">
                    <Select
                      value={reservation.selector}
                      onChange={(event) =>
                        setReservation({ ...reservation, selector: event.target.value })
                      }
                    >
                      <option value="contamination_safe">contamination_safe</option>
                      <option value="heuristic">heuristic</option>
                      <option value="random">random</option>
                    </Select>
                  </Field>
                  <Field label="Evaluation percent">
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
                  <Field label="Maximum samples">
                    <Input
                      type="number"
                      min="0"
                      value={reservation.max_samples}
                      onChange={(event) =>
                        setReservation({ ...reservation, max_samples: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Exact target (optional)">
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
                  <Field label="Reservation seed">
                    <Input
                      type="number"
                      value={reservation.seed}
                      onChange={(event) =>
                        setReservation({ ...reservation, seed: event.target.value })
                      }
                    />
                  </Field>
                  <Field label="Contamination scan">
                    <Select
                      value={reservation.contamination_scope}
                      onChange={(event) =>
                        setReservation({ ...reservation, contamination_scope: event.target.value })
                      }
                    >
                      <option value="registry">All ready batches</option>
                      <option value="composition">This composition only</option>
                    </Select>
                  </Field>
                  <Checkbox
                    label="I understand this is irreversible"
                    checked={reservation.confirmed}
                    onChange={(confirmed) =>
                      setReservation({ ...reservation, confirmed })
                    }
                  />
                  <HStack vAlign="end">
                    <Button disabled={reserve.isPending || !reservation.confirmed}>
                      Start reservation
                    </Button>
                  </HStack>
                </Grid>
              </form>
            </VStack>
          </Card>

          <Grid columns={2} gap={4}>
            <Card>
              <VStack gap={3}>
                <Heading level={4}>Reservation history</Heading>
                <VStack gap={2}>
                  {reservations.data?.length ? (
                    reservations.data.map((item) => (
                      <Card key={item.id}>
                        <VStack gap={2}>
                          <HStack vAlign="center" hAlign="between">
                            <Text type="body" weight="medium">Reservation {item.id} · {item.selector}</Text>
                            <Badge>{item.status}</Badge>
                          </HStack>
                          {item.error && <Text type="body" color="secondary">{item.error}</Text>}
                          {item.report && (
                            <CodeBlock
                              language="json"
                              code={JSON.stringify(item.report, null, 2)}
                            />
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
            <Card>
              <VStack gap={3}>
                <Heading level={4}>Trainable statistics</Heading>
                <CodeBlock
                  language="json"
                  code={stats.isLoading ? 'Loading…' : JSON.stringify(stats.data, null, 2)}
                />
              </VStack>
            </Card>
          </Grid>

          <Card>
            <VStack gap={3}>
              <Heading level={4}>Trainable preview</Heading>
              <DataTable
                loading={preview.isLoading}
                rows={preview.data}
                columns={[
                  { key: 'sample_id', header: 'ID' },
                  { key: 'source_text', header: 'Source' },
                  { key: 'target_text', header: 'Target' },
                ]}
              />
            </VStack>
          </Card>
        </VStack>
      )}
    </VStack>
  )
}
