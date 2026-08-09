import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { HStack, VStack } from '@astryxdesign/core/Stack'
import { Grid } from '@astryxdesign/core/Grid'
import { Heading, Text } from '@astryxdesign/core/Text'
import { Spinner } from '@astryxdesign/core/Spinner'

import { DataTable } from '@/components/DataTable'
import { Badge, Button, Card, Field, Input, PageHeader, Select } from '@/components/ui'
import { api } from '@/lib/api'
import type { Allocation, Row, Sample } from '@/types'

type Query = {
  q: string
  batch_id: string
  src_lang: string
  tgt_lang: string
  domain: string
  allocation: string
}

const EMPTY: Query = { q: '', batch_id: '', src_lang: '', tgt_lang: '', domain: '', allocation: '' }

function LanguagePairChip({ src, tgt }: { src?: string; tgt?: string }) {
  if (!src && !tgt) return <Text type="supporting" color="secondary">—</Text>
  return (
    <HStack gap={1} vAlign="center" style={{ display: 'inline-flex', alignItems: 'center' }}>
      <span
        style={{
          padding: '2px 6px',
          borderRadius: '4px',
          fontSize: '0.75rem',
          fontWeight: 600,
          letterSpacing: '0.04em',
          backgroundColor: 'rgba(59, 130, 246, 0.12)',
          color: '#2563eb',
          fontFamily: 'monospace',
        }}
      >
        {(src || '?').toUpperCase()}
      </span>
      <Text type="supporting" color="secondary" style={{ fontSize: '0.75rem' }}>
        ➔
      </Text>
      <span
        style={{
          padding: '2px 6px',
          borderRadius: '4px',
          fontSize: '0.75rem',
          fontWeight: 600,
          letterSpacing: '0.04em',
          backgroundColor: 'rgba(16, 185, 129, 0.12)',
          color: '#059669',
          fontFamily: 'monospace',
        }}
      >
        {(tgt || '?').toUpperCase()}
      </span>
    </HStack>
  )
}

function AllocationBadge({ allocation }: { allocation?: string }) {
  const text = String(allocation ?? 'UNKNOWN')
  let variant: 'success' | 'warning' | 'error' | 'neutral' = 'neutral'
  if (text === 'TRAINABLE') variant = 'success'
  else if (text === 'RESERVED_EVALUATION') variant = 'warning'
  else if (text === 'QUARANTINED') variant = 'error'
  else if (text === 'IGNORED') variant = 'neutral'

  const labels: Record<string, string> = {
    TRAINABLE: 'Trainable',
    RESERVED_EVALUATION: 'Reserved (Eval)',
    QUARANTINED: 'Quarantined',
    IGNORED: 'Ignored',
  }

  return <Badge variant={variant}>{labels[text] ?? text}</Badge>
}

export default function Samples() {
  const [query, setQuery] = useState<Query>(EMPTY)
  const [applied, setApplied] = useState<Query>(EMPTY)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [page, setPage] = useState(0)
  const limit = 50

  const searching = applied.q.trim().length > 0
  const activeAdvancedCount = [
    applied.batch_id,
    applied.src_lang,
    applied.tgt_lang,
    applied.domain,
  ].filter((val) => val.trim().length > 0).length

  const list = useQuery<{ items: Row[]; total: number | null }>({
    queryKey: ['samples', applied, page],
    queryFn: async () => {
      if (searching) {
        const items = await api.get<Row[]>('/search', {
          ...applied,
          limit,
          offset: page * limit,
        })
        return { items, total: null }
      }
      const page_ = await api.get<{ items: Sample[]; total: number }>('/samples', {
        ...applied,
        q: undefined,
        limit,
        offset: page * limit,
      })
      return { items: page_.items as unknown as Row[], total: page_.total }
    },
  })

  const [selected, setSelected] = useState<number | null>(null)
  const detail = useQuery({
    queryKey: ['sample', selected],
    queryFn: () => api.get<Sample>(`/samples/${selected}`),
    enabled: selected !== null,
  })

  // Safety Confirmation Modal state
  const [pendingReallocate, setPendingReallocate] = useState<{
    sampleId: number
    targetAllocation: Allocation
  } | null>(null)
  const [reallocateLoading, setReallocateLoading] = useState(false)
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; message: string } | null>(null)

  const qc = useQueryClient()

  const executeReallocation = async () => {
    if (!pendingReallocate) return
    setReallocateLoading(true)
    setFeedback(null)
    try {
      await api.post('/samples/allocation', {
        sample_ids: [pendingReallocate.sampleId],
        allocation: pendingReallocate.targetAllocation,
      })
      await qc.invalidateQueries({ queryKey: ['sample', pendingReallocate.sampleId] })
      await qc.invalidateQueries({ queryKey: ['samples'] })
      setFeedback({
        type: 'success',
        message: `Sample #${pendingReallocate.sampleId} allocation updated to ${pendingReallocate.targetAllocation}.`,
      })
      setPendingReallocate(null)
    } catch (err: unknown) {
      setFeedback({
        type: 'error',
        message: `Failed to update allocation: ${(err as Error).message ?? String(err)}`,
      })
    } finally {
      setReallocateLoading(false)
    }
  }

  const promptReallocate = (sampleId: number, targetAllocation: Allocation) => {
    setPendingReallocate({ sampleId, targetAllocation })
    setFeedback(null)
  }

  return (
    <VStack gap={4}>
      <PageHeader
        title="Samples"
        subtitle="Immutable translation units. Manage allocation splits and inspect parallel sentence pairs."
      />

      {feedback && (
        <Card padding={3}>
          <HStack vAlign="center" hAlign="between">
            <Text
              type="body"
              weight="medium"
              style={{ color: feedback.type === 'error' ? '#dc2626' : '#16a34a' }}
            >
              {feedback.message}
            </Text>
            <Button variant="ghost" onClick={() => setFeedback(null)}>
              Dismiss
            </Button>
          </HStack>
        </Card>
      )}

      {/* Filter Header */}
      <Card padding={4}>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            setPage(0)
            setApplied(query)
          }}
        >
          <VStack gap={3}>
            {/* Primary Filter Row */}
            <Grid columns={12} gap={3} align="end">
              <div style={{ gridColumn: 'span 5' }}>
                <Field label="Text Search">
                  <Input
                    placeholder="Search source or target translation text..."
                    value={query.q}
                    onChange={(e) => setQuery({ ...query, q: e.target.value })}
                  />
                </Field>
              </div>

              <div style={{ gridColumn: 'span 3' }}>
                <Field label="Allocation">
                  <Select
                    value={query.allocation}
                    onChange={(e) => setQuery({ ...query, allocation: e.target.value })}
                  >
                    <option value="">All Allocations</option>
                    <option value="TRAINABLE">Trainable</option>
                    <option value="RESERVED_EVALUATION">Reserved (Evaluation)</option>
                    <option value="QUARANTINED">Quarantined</option>
                    <option value="IGNORED">Ignored</option>
                  </Select>
                </Field>
              </div>

              <div style={{ gridColumn: 'span 4' }}>
                <HStack gap={2} vAlign="end" hAlign="end">
                  <Button type="submit">Search</Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => {
                      setQuery(EMPTY)
                      setApplied(EMPTY)
                      setPage(0)
                    }}
                  >
                    Reset
                  </Button>
                  <Button
                    type="button"
                    variant="ghost"
                    onClick={() => setShowAdvanced(!showAdvanced)}
                  >
                    {showAdvanced ? 'Hide Filters' : 'Filters'}
                    {activeAdvancedCount > 0 && ` (${activeAdvancedCount})`}
                  </Button>
                </HStack>
              </div>
            </Grid>

            {/* Collapsible Advanced Filters */}
            {showAdvanced && (
              <div
                style={{
                  paddingTop: '1rem',
                  borderTop: '1px solid rgba(0, 0, 0, 0.08)',
                }}
              >
                <Grid columns={4} gap={3}>
                  <Field label="Batch ID">
                    <Input
                      placeholder="e.g. 101"
                      value={query.batch_id}
                      onChange={(e) => setQuery({ ...query, batch_id: e.target.value })}
                    />
                  </Field>
                  <Field label="Source Language">
                    <Input
                      placeholder="e.g. en"
                      value={query.src_lang}
                      onChange={(e) => setQuery({ ...query, src_lang: e.target.value })}
                    />
                  </Field>
                  <Field label="Target Language">
                    <Input
                      placeholder="e.g. de"
                      value={query.tgt_lang}
                      onChange={(e) => setQuery({ ...query, tgt_lang: e.target.value })}
                    />
                  </Field>
                  <Field label="Domain">
                    <Input
                      placeholder="e.g. medical, legal"
                      value={query.domain}
                      onChange={(e) => setQuery({ ...query, domain: e.target.value })}
                    />
                  </Field>
                </Grid>
              </div>
            )}
          </VStack>
        </form>
      </Card>

      {/* Unified Table View */}
      <DataTable
        loading={list.isLoading}
        rows={(list.data?.items ?? []) as unknown as Row[]}
        empty="No translation units match the selected criteria."
        columns={[
          {
            key: 'id',
            header: 'ID',
            width: '90px',
            render: (r) => (
              <Text type="body" weight="medium" style={{ fontFamily: 'monospace' }}>
                #{String(r.id ?? r.sample_id)}
              </Text>
            ),
          },
          {
            key: 'pair',
            header: 'Pair',
            width: '130px',
            render: (r) => (
              <LanguagePairChip
                src={String(r.src_lang ?? '')}
                tgt={String(r.tgt_lang ?? '')}
              />
            ),
          },
          {
            key: 'text_preview',
            header: 'Parallel Sentence Preview',
            render: (r) => {
              const srcText = String(r.source_text ?? '')
              const tgtText = String(r.target_text ?? '')
              if (srcText || tgtText) {
                return (
                  <VStack gap={1}>
                    <Text
                      type="body"
                      style={{
                        fontSize: '0.875rem',
                        lineHeight: '1.3',
                        display: '-webkit-box',
                        WebkitLineClamp: 1,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}
                    >
                      <span style={{ fontWeight: 600, color: 'var(--color-text-secondary, #6b7280)' }}>
                        SRC:{' '}
                      </span>
                      {srcText || '—'}
                    </Text>
                    <Text
                      type="supporting"
                      color="secondary"
                      style={{
                        fontSize: '0.8125rem',
                        lineHeight: '1.3',
                        display: '-webkit-box',
                        WebkitLineClamp: 1,
                        WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}
                    >
                      <span style={{ fontWeight: 600 }}>TGT: </span>
                      {tgtText || '—'}
                    </Text>
                  </VStack>
                )
              }
              return (
                <Text type="supporting" color="secondary">
                  Batch #{String(r.batch_id ?? '—')} • Click View for full text alignment
                </Text>
              )
            },
          },
          {
            key: 'domain',
            header: 'Domain',
            width: '120px',
            render: (r) => (
              <Text type="supporting" color="secondary">
                {String(r.domain ?? 'general')}
              </Text>
            ),
          },
          {
            key: 'quality',
            header: 'Quality',
            width: '90px',
            render: (r) => {
              const q = r.quality
              if (typeof q === 'number') {
                return (
                  <Text type="body" weight="medium" style={{ fontFamily: 'monospace' }}>
                    {q.toFixed(2)}
                  </Text>
                )
              }
              return <Text type="supporting" color="secondary">—</Text>
            },
          },
          {
            key: 'allocation',
            header: 'Allocation',
            width: '140px',
            render: (r) => <AllocationBadge allocation={String(r.allocation ?? 'TRAINABLE')} />,
          },
          {
            key: 'actions',
            header: '',
            width: '90px',
            render: (r) => {
              const sampleId = Number(r.id ?? r.sample_id)
              return (
                <Button
                  variant="ghost"
                  onClick={() => setSelected(sampleId)}
                >
                  View
                </Button>
              )
            },
          },
        ]}
      />

      {/* Pagination & Status Bar */}
      <HStack vAlign="center" hAlign="between" style={{ padding: '0.5rem 0' }}>
        <HStack gap={2} vAlign="center">
          {list.data?.total != null ? (
            <Text type="supporting" color="secondary">
              Showing {page * limit + 1}–
              {Math.min((page + 1) * limit, list.data.total)} of{' '}
              {list.data.total.toLocaleString()} samples
            </Text>
          ) : (
            <Text type="supporting" color="secondary">
              Showing {list.data?.items?.length ?? 0} matching search results on page {page + 1}
            </Text>
          )}
        </HStack>

        <HStack gap={2} vAlign="center">
          <Button
            variant="ghost"
            disabled={page === 0 || list.isLoading}
            onClick={() => setPage(page - 1)}
          >
            Previous
          </Button>
          <Text type="body" weight="medium" style={{ padding: '0 0.5rem' }}>
            Page {page + 1}
          </Text>
          <Button
            variant="ghost"
            disabled={
              list.isLoading ||
              (list.data?.items ?? []).length < limit ||
              (list.data?.total != null && (page + 1) * limit >= list.data.total)
            }
            onClick={() => setPage(page + 1)}
          >
            Next
          </Button>
        </HStack>
      </HStack>

      {/* Reallocation Safety Confirmation Modal */}
      {pendingReallocate && (
        <Card padding={5} style={{ border: '2px solid var(--color-border-warning, #f59e0b)' }}>
          <VStack gap={3}>
            <Heading level={3}>Confirm Allocation Change</Heading>
            <Text type="body">
              Are you sure you want to change allocation for{' '}
              <strong>Sample #{pendingReallocate.sampleId}</strong> to{' '}
              <strong>{pendingReallocate.targetAllocation}</strong>?
            </Text>
            {pendingReallocate.targetAllocation === 'RESERVED_EVALUATION' && (
              <Text type="supporting" color="secondary">
                ⚠️ Reserving a sample permanently excludes it from model training candidate pools to preserve evaluation split benchmark integrity.
              </Text>
            )}
            {pendingReallocate.targetAllocation === 'QUARANTINED' && (
              <Text type="supporting" color="secondary">
                ⚠️ Quarantining flags this sample as contaminated or invalid.
              </Text>
            )}

            <HStack gap={3} hAlign="end" style={{ marginTop: '0.5rem' }}>
              <Button
                variant="ghost"
                disabled={reallocateLoading}
                onClick={() => setPendingReallocate(null)}
              >
                Cancel
              </Button>
              <Button
                variant={pendingReallocate.targetAllocation === 'QUARANTINED' ? 'danger' : 'primary'}
                disabled={reallocateLoading}
                onClick={() => void executeReallocation()}
              >
                {reallocateLoading ? 'Updating...' : 'Confirm Reallocation'}
              </Button>
            </HStack>
          </VStack>
        </Card>
      )}

      {/* Parallel Translation Unit Detail View */}
      {selected !== null && (
        <Card padding={5}>
          <VStack gap={4}>
            <HStack vAlign="center" hAlign="between">
              <HStack gap={3} vAlign="center">
                <Heading level={3}>Sample #{selected}</Heading>
                {detail.data?.allocation && (
                  <AllocationBadge allocation={detail.data.allocation} />
                )}
              </HStack>
              <Button variant="ghost" onClick={() => setSelected(null)}>
                Close Panel
              </Button>
            </HStack>

            {detail.isLoading ? (
              <VStack hAlign="center" vAlign="center" style={{ padding: '2rem 0' }}>
                <Spinner size="md" />
              </VStack>
            ) : detail.data ? (
              <VStack gap={4}>
                {/* Parallel Translation Alignment Card */}
                <Grid columns={2} gap={4}>
                  <Card padding={4} style={{ backgroundColor: 'rgba(0, 0, 0, 0.02)' }}>
                    <VStack gap={2}>
                      <HStack vAlign="center" hAlign="between">
                        <Text type="supporting" color="secondary" weight="medium">
                          SOURCE TEXT
                        </Text>
                        <LanguagePairChip src={detail.data.src_lang} />
                      </HStack>
                      <Text
                        type="body"
                        style={{
                          fontSize: '1rem',
                          lineHeight: '1.5',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {detail.data.source_text || '—'}
                      </Text>
                    </VStack>
                  </Card>

                  <Card padding={4} style={{ backgroundColor: 'rgba(0, 0, 0, 0.02)' }}>
                    <VStack gap={2}>
                      <HStack vAlign="center" hAlign="between">
                        <Text type="supporting" color="secondary" weight="medium">
                          TARGET TEXT
                        </Text>
                        <LanguagePairChip tgt={detail.data.tgt_lang} />
                      </HStack>
                      <Text
                        type="body"
                        style={{
                          fontSize: '1rem',
                          lineHeight: '1.5',
                          whiteSpace: 'pre-wrap',
                          wordBreak: 'break-word',
                        }}
                      >
                        {detail.data.target_text || '—'}
                      </Text>
                    </VStack>
                  </Card>
                </Grid>

                {/* Metadata Summary Grid */}
                <Grid columns={4} gap={3}>
                  <VStack gap={1}>
                    <Text type="supporting" color="secondary">
                      Batch ID
                    </Text>
                    <Text type="body" weight="medium">
                      #{detail.data.batch_id}
                    </Text>
                  </VStack>

                  <VStack gap={1}>
                    <Text type="supporting" color="secondary">
                      Domain
                    </Text>
                    <Text type="body" weight="medium">
                      {detail.data.domain ?? 'general'}
                    </Text>
                  </VStack>

                  <VStack gap={1}>
                    <Text type="supporting" color="secondary">
                      Quality Metric
                    </Text>
                    <Text type="body" weight="medium">
                      {typeof detail.data.quality === 'number'
                        ? detail.data.quality.toFixed(3)
                        : '—'}
                    </Text>
                  </VStack>

                  <VStack gap={1}>
                    <Text type="supporting" color="secondary">
                      Source ID
                    </Text>
                    <Text type="body" weight="medium">
                      {detail.data.source_id ? `#${detail.data.source_id}` : '—'}
                    </Text>
                  </VStack>
                </Grid>

                {/* Reallocation Actions */}
                <Card padding={3} style={{ backgroundColor: 'rgba(0, 0, 0, 0.015)' }}>
                  <HStack vAlign="center" hAlign="between">
                    <VStack gap={1}>
                      <Text type="body" weight="medium">
                        Allocation Actions
                      </Text>
                      <Text type="supporting" color="secondary">
                        Safely adjust dataset allocation split for Sample #{selected}
                      </Text>
                    </VStack>

                    <HStack gap={2}>
                      {detail.data.allocation !== 'TRAINABLE' && (
                        <Button
                          variant="ghost"
                          onClick={() => promptReallocate(selected, 'TRAINABLE')}
                        >
                          Mark Trainable
                        </Button>
                      )}
                      {detail.data.allocation !== 'RESERVED_EVALUATION' && (
                        <Button
                          variant="ghost"
                          onClick={() => promptReallocate(selected, 'RESERVED_EVALUATION')}
                        >
                          Reserve for Evaluation
                        </Button>
                      )}
                      {detail.data.allocation !== 'QUARANTINED' && (
                        <Button
                          variant="ghost"
                          onClick={() => promptReallocate(selected, 'QUARANTINED')}
                        >
                          Quarantine
                        </Button>
                      )}
                      {detail.data.allocation !== 'IGNORED' && (
                        <Button
                          variant="ghost"
                          onClick={() => promptReallocate(selected, 'IGNORED')}
                        >
                          Ignore
                        </Button>
                      )}
                    </HStack>
                  </HStack>
                </Card>
              </VStack>
            ) : (
              <Text type="supporting" color="secondary">
                Failed to load sample details.
              </Text>
            )}
          </VStack>
        </Card>
      )}
    </VStack>
  )
}

